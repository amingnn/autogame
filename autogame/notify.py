"""收集任务报告、写入通知日志并推送 Server 酱。"""

from __future__ import annotations

import threading

import requests

from autogame.logger import mlog, notify_logger


_report: list[str] = []
_report_lock = threading.Lock()
NOTIFICATION_TITLE = "自动化任务报告"


def clear_report() -> None:
    """清空上一轮自动化会话留下的通知内容。"""

    with _report_lock:
        _report.clear()


def report(content: str) -> None:
    """保存一段任务报告，并写入独立的通知日志。"""

    report_sections([content])


def report_sections(contents: list[str]) -> None:
    """批量保存多段任务报告，并只记录一次主日志。"""

    if not contents:
        return
    with _report_lock:
        _report.extend(contents)
    for content in contents:
        notify_logger.info("{}", content)
    mlog.info("通知报告已写入通知日志，共 {} 段", len(contents))


def _to_code_block(text: str) -> str:
    """把通知内容包裹为 Markdown 代码块。"""

    return f"```\n{text}\n```"


def build_push_payload(reports: list[str]) -> dict[str, str]:
    """构造 Server 酱实际接收的标题和 Markdown 正文。"""

    return {
        "title": NOTIFICATION_TITLE,
        "desp": "\n\n".join(_to_code_block(item) for item in reports),
    }


def push_wechat(send_key: str) -> None:
    """把本次会话报告推送到 Server 酱。"""

    with _report_lock:
        reports = list(_report)
    if not reports:
        mlog.warning("推送内容为空，跳过")
        return

    url = f"https://sctapi.ftqq.com/{send_key}.send"
    params = build_push_payload(reports)
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
