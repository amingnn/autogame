"""创建 FastAPI 应用，并连接桌面页面、任务调度器和 Webhook。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.common import LauncherConfig
from core.config_store import ConfigConflictError
from core.logger import mlog
from core.notify import notify_wrapper, report

if TYPE_CHECKING:
    from core.scheduler import Scheduler


class RunTaskRequest(BaseModel):
    """任务启动请求。"""

    force: bool = False


class TaskPatchRequest(BaseModel):
    """页面允许修改的任务配置字段。"""

    enabled: bool | None = None
    interval_hours: float | None = Field(default=None, ge=0)
    launcher: LauncherConfig | None = None
    config_revision: str | None = None

    def to_patch(self) -> dict[str, object]:
        """返回不包含空值和版本字段的配置补丁。"""

        data = self.model_dump(exclude_none=True)
        data.pop("config_revision", None)
        return data


class SystemPatchRequest(BaseModel):
    """页面允许修改的全局配置字段。"""

    log_level: str | None = None
    webhook_port: int | None = Field(default=None, ge=1, le=65535)
    shutdown_on_complete: bool | None = None
    shutdown_delay_seconds: int | None = Field(default=None, ge=0, le=86400)
    shutdown_timeout_hours: float | None = Field(default=None, gt=0, le=168)
    completion_action: Literal["shutdown", "sleep", "none", "hibernate"] | None = None
    server_chan_key: str | None = None
    clear_server_chan_key: bool = False
    config_revision: str | None = None

    def to_patch(self) -> dict[str, object]:
        """生成全局配置补丁，默认不覆盖已有 SendKey。"""

        data = self.model_dump(exclude_none=True)
        data.pop("config_revision", None)
        clear_key = bool(data.pop("clear_server_chan_key", False))
        if clear_key:
            data["server_chan_key"] = ""
        elif not data.get("server_chan_key"):
            data.pop("server_chan_key", None)
        return data


def _is_local_request(request: Request) -> bool:
    """判断管理接口请求是否来自本机。"""

    client = request.client
    return client is not None and client.host in {"127.0.0.1", "::1", "localhost"}


def _refine_maa_message(payload: dict) -> str:
    """清理 MAA 推送内容，减少无关日志。"""

    content = str(payload.get("content", payload.get("msg", "")))
    content = content.replace("[TraceLogBrush]", " ")
    content = re.sub(
        r"^.*?Resource Time:\s*\n\d{4}/\d{1,2}/\d{1,2} \d{2}:\d{2}:\d{2}\s*\n",
        "",
        content,
        flags=re.DOTALL,
    )
    for keyword in ("任务已全部完成！", "任务已全部完成!"):
        end = content.find(keyword)
        if end >= 0:
            content = content[: end + len(keyword)]
            break
    title = str(payload.get("title", "明日方舟自动化任务报告"))
    return notify_wrapper(content=content, title=title)


def _recent_logs(log_dir: Path, limit: int = 100) -> list[str]:
    """读取最新主日志的最后若干行。"""

    files = sorted(
        (path for path in log_dir.glob("*.log") if not path.name.startswith("notify-")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return []
    try:
        return files[0].read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []


def create_app(scheduler: "Scheduler") -> FastAPI:
    """创建并配置 AutoGame 的 FastAPI 应用。"""

    app = FastAPI(title="AutoGame 任务服务")
    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/ui", StaticFiles(directory=web_dir, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        """把根路径跳转到桌面管理页面。"""

        return RedirectResponse("/ui/")

    @app.post("/trigger")
    async def trigger(payload: dict = Body(...)) -> dict[str, object]:
        """兼容原有通用任务触发接口。"""

        task_name = str(payload.get("trigger", ""))
        force = bool(payload.get("force", False))
        if not task_name:
            return {"status": "error", "message": "缺少 trigger 字段"}
        if task_name not in scheduler.config.tasks:
            return {"status": "error", "message": f"未知任务：{task_name}"}

        mlog.info("收到任务触发：{}，强制执行：{}", task_name, force)
        asyncio.create_task(scheduler.run_task(task_name, force=force))
        return {"status": "accepted", "task": task_name}

    @app.api_route("/maa", methods=["GET", "POST"])
    async def maa(
        request: Request,
        payload: dict | None = Body(None),
    ) -> dict[str, str]:
        """处理 MAA 和 maaend 的完成回调。"""

        if request.method == "GET":
            message = dict(request.query_params).get("msg", "没有附加消息")
            report(notify_wrapper(message, title="终末地自动化任务"))
            completed = scheduler.mark_done("maaend")
            return {"status": "ok" if completed else "ignored"}

        if payload:
            report(_refine_maa_message(payload))
            completed = scheduler.mark_done("maa")
            return {"status": "ok" if completed else "ignored"}
        return {"status": "fail"}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, object]:
        """返回当前服务和任务状态。"""

        if not _is_local_request(request):
            raise HTTPException(status_code=403, detail="管理接口只允许本机访问")
        return scheduler.get_status_snapshot()

    @app.post("/api/tasks/{task_name}/run")
    async def run_task(
        task_name: str,
        request: Request,
        body: RunTaskRequest | None = Body(None),
    ) -> dict[str, object]:
        """从桌面页面启动一个任务。"""

        if not _is_local_request(request):
            raise HTTPException(status_code=403, detail="管理接口只允许本机访问")
        if task_name not in scheduler.config.tasks:
            raise HTTPException(status_code=404, detail="任务不存在")
        force = body.force if body else False
        accepted = await scheduler.run_task(task_name, force=force)
        return {
            "accepted": accepted,
            "message": "任务已启动" if accepted else "任务未启动，请检查当前状态",
            "task": scheduler.get_status_snapshot(),
        }

    @app.patch("/api/tasks/{task_name}")
    async def update_task(
        task_name: str,
        request: Request,
        body: TaskPatchRequest,
    ) -> dict[str, object]:
        """保存任务配置并重新加载当前调度器。"""

        if not _is_local_request(request):
            raise HTTPException(status_code=403, detail="管理接口只允许本机访问")
        try:
            task = scheduler.update_task_config(
                task_name,
                body.to_patch(),
                expected_revision=body.config_revision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except ConfigConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "ok", "task": task}

    @app.patch("/api/config/system")
    async def update_system(
        request: Request,
        body: SystemPatchRequest,
    ) -> dict[str, object]:
        """保存页面提交的全局配置。"""

        if not _is_local_request(request):
            raise HTTPException(status_code=403, detail="管理接口只允许本机访问")
        try:
            system = scheduler.update_system_config(
                body.to_patch(),
                expected_revision=body.config_revision,
            )
        except ConfigConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        message = "全局配置已保存"
        if body.webhook_port is not None:
            message += "；端口变更将在重启后生效"
        return {"status": "ok", "message": message, "system": system}

    @app.post("/api/config/reload")
    async def reload_config(request: Request) -> dict[str, object]:
        """重新加载磁盘上的配置文件。"""

        if not _is_local_request(request):
            raise HTTPException(status_code=403, detail="管理接口只允许本机访问")
        try:
            return scheduler.reload_config()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"配置重载失败：{exc}") from exc

    @app.get("/api/logs/recent")
    async def recent_logs(request: Request) -> dict[str, object]:
        """返回最近主日志内容。"""

        if not _is_local_request(request):
            raise HTTPException(status_code=403, detail="管理接口只允许本机访问")
        return {"lines": _recent_logs(scheduler.config.log_dir)}

    return app
