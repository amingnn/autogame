"""收集任务报告、写入通知日志并推送 Server 酱。"""

from __future__ import annotations

import threading

import requests

from core.logger import mlog, notify_logger


_report: list[str] = []
_report_lock = threading.Lock()


def report(content: str) -> None:
    """保存一段任务报告，并写入独立的通知日志。"""

    with _report_lock:
        _report.append(content)
    notify_logger.info("{}", content)
    mlog.info("通知报告已写入通知日志")


def notify_wrapper(content: str, title: str | None = None) -> str:
    """为通知内容添加标题和分隔线。"""

    separator = "=" * 10
    header = f"{separator}{title}{separator}" if title else separator * 3
    return f"{header}\n{content.strip()}\n{separator * 3}"


def _to_code_block(text: str) -> str:
    """把通知内容包裹为 Markdown 代码块。"""

    return f"```\n{text}\n```"


def push_wechat(send_key: str) -> None:
    """把本次会话报告推送到 Server 酱。"""

    with _report_lock:
        reports = list(_report)
    if not reports:
        mlog.warning("推送内容为空，跳过")
        return

    content = "\n\n".join(_to_code_block(item) for item in reports)
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    params = {
        "title": "自动化任务报告",
        "desp": content,
    }
    try:
        response = requests.post(
            url,
            json=params,
            headers={"Content-Type": "application/json;charset=utf-8"},
            timeout=10,
        )
        response.raise_for_status()
        mlog.info("Server 酱推送成功")
    except requests.RequestException as exc:
        mlog.error("Server 酱推送失败：{}", exc)
