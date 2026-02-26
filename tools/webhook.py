import re
import uvicorn
from fastapi import FastAPI, Body, Request
from .logger import log_wrapper, mlog_info, time_wrapper  # 导入我们提取的 logger
from .config import status

app = FastAPI()


def _refine_message(payload: dict) -> str:
    # 注意：这里 payload 已经由接口传入，不需要在函数内写死
    content = payload.get("content", "")

    # 1. 优先全局替换标签为空
    content = content.replace("[TraceLogBrush]", " ")

    # 2. 去除开头到 Resource Time 块
    content = re.sub(
        r'^.*?Resource Time:\s*\n\d{4}/\d{1,2}/\d{1,2} \d{2}:\d{2}:\d{2}\s*\n',
        '',
        content,
        flags=re.DOTALL
    )

    # 3. 压缩“当前设施”行
    facilities = re.findall(r'(\[\d{2}:\d{2}:\d{2}\])当前设施:', content)
    if facilities:
        start_time = facilities[0]
        end_time = facilities[-1]
        summary_text = f"{start_time} - {end_time}查看当前设备...\n"
        content = re.sub(
            r'\[\d{2}:\d{2}:\d{2}\]当前设施:.*\[\d{2}:\d{2}:\d{2}\]当前设施:.*?\n',
            summary_text,
            content,
            flags=re.DOTALL
        )

    # 4. 精准截断
    end_keyword = "任务已全部完成！"
    end_pos = content.find(end_keyword)
    if end_pos != -1:
        content = content[:end_pos + len(end_keyword)]

    title = payload.get('title', "明日方舟任务报告")

    return log_wrapper(title=title, content=content)


@app.api_route("/maa", methods=["GET", "POST"])
async def maa(request: Request, payload=Body(None)):
    if request.method == "GET":
        params = dict(request.query_params)
        log_msg = params.get("msg", "无")
        mlog_info(log_wrapper(time_wrapper(log_msg), title="终末地自动化任务"))
        status["end"] = True
        return {"status": "ok"}

    if request.method == "POST":
        if payload:
            refined_content = _refine_message(payload)
            mlog_info(refined_content)  # 写入本地日志
            status["maa"] = True
            return {"status": "success"}
    return {"status": "fail"}


if __name__ == '__main__':
  uvicorn.run("webhook:app", host="127.0.0.1", port=8000, reload=True)
