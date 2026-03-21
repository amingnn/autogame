import re
from typing import TYPE_CHECKING

from fastapi import FastAPI, Body, Request

from core.logger import mlog, report, log_wrapper

if TYPE_CHECKING:
    from core.scheduler import Scheduler


def _refine_maa_message(payload: dict) -> str:
    """清洗 MAA 推送的日志内容，减少冗余信息。"""
    content = payload.get("content", "")

    content = content.replace("[TraceLogBrush]", " ")

    content = re.sub(
        r'^.*?Resource Time:\s*\n\d{4}/\d{1,2}/\d{1,2} \d{2}:\d{2}:\d{2}\s*\n',
        "",
        content,
        flags=re.DOTALL,
    )

    facilities = re.findall(r'(\[\d{2}:\d{2}:\d{2}\])当前设施:', content)
    if facilities:
        start_ts = facilities[0]
        end_ts = facilities[-1]
        summary = f"{start_ts} - {end_ts}查看当前设备...\n"
        content = re.sub(
            r'\[\d{2}:\d{2}:\d{2}\]当前设施:.*\[\d{2}:\d{2}:\d{2}\]当前设施:.*?\n',
            summary,
            content,
            flags=re.DOTALL,
        )

    end_keyword = "任务已全部完成！"
    end_pos = content.find(end_keyword)
    if end_pos != -1:
        content = content[: end_pos + len(end_keyword)]

    title = payload.get("title", "明日方舟任务报告")
    return log_wrapper(content=content, title=title)


def create_app(scheduler: "Scheduler") -> FastAPI:
    app = FastAPI(title="AutoGame Webhook")

    @app.post("/done")
    async def done(payload: dict = Body(...)):
        """任务自报完成接口（供 run() 内部调用）。
        请求体: {"task": "<task_name>"}
        """
        task_name = payload.get("task", "")
        if not task_name:
            return {"status": "error", "message": "缺少 task 字段"}
        scheduler.mark_done(task_name)
        return {"status": "ok", "task": task_name}

    @app.post("/trigger")
    async def trigger(payload: dict = Body(...)):
        """通用任务触发接口。
        请求体: {"trigger": "<task_name>", "force": true}
        """
        task_name = payload.get("trigger", "")
        force = bool(payload.get("force", False))

        if not task_name:
            return {"status": "error", "message": "缺少 trigger 字段"}

        if task_name not in scheduler.config.tasks:
            return {"status": "error", "message": f"未知任务: {task_name}"}

        mlog.info(f"Webhook 触发任务: {task_name} (force={force})")

        import asyncio
        asyncio.create_task(scheduler.run_task(task_name, force=force))
        return {"status": "accepted", "task": task_name}

    @app.api_route("/maa", methods=["GET", "POST"])
    async def maa(request: Request, payload: dict | None = Body(None)):
        """MAA / 终末地回调接口。
        POST /maa  → MAA 明日方舟任务完成回调，标记 maa 任务完成
        GET  /maa  → 终末地任务完成回调，标记 maaend 任务完成
        """
        if request.method == "GET":
            params = dict(request.query_params)
            log_msg = params.get("msg", "无")
            report(log_wrapper(log_msg, title="终末地自动化任务"))
            scheduler.mark_done("maaend")
            return {"status": "ok"}

        if request.method == "POST" and payload:
            report(_refine_maa_message(payload))
            scheduler.mark_done("maa")
            return {"status": "success"}

        return {"status": "fail"}

    return app
