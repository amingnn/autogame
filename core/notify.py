# 通知
import threading

import requests
from core.logger import mlog

# ── 推送报告收集器 ─────────────────────────────────────────────────────────────
# 只收集有业务含义的格式化内容（签到结果、MAA 日志、超时提示等）
# 加锁保证 skyland 线程与 webhook 事件循环同时写入时顺序不乱
_report: list[str] = []
_report_lock = threading.Lock()


def report(content: str) -> None:
    """写入系统日志，同时原子追加到本次会话的推送报告中。"""
    mlog.info(content)
    with _report_lock:
        _report.append(content)


# ── 格式工具 ───────────────────────────────────────────────────────────────────

def notify_wrapper(content: str, title: str | None = None) -> str:
    """给每个任务通知生成带标题分隔线的纯文本段落（日志 / 收集用）。"""
    frt = "=" * 10
    header = f"{frt}{title}{frt}" if title else frt * 3
    footer = frt * 3
    return f"{header}\n{content.strip()}\n{footer}"


def _to_code_block(text: str) -> str:
    """推送前将段落包入代码围栏，避免 Server酱 解析 [] 等 Markdown 语法。"""
    return f"```\n{text}\n```"


def push_wechat(send_key: str) -> None:
    """将本次会话收集到的任务报告推送到 Server 酱。"""
    if not _report:
        mlog.warning("推送内容为空，跳过")
        return
    content = "\n\n".join(_to_code_block(item) for item in _report)
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    headers = {"Content-Type": "application/json;charset=utf-8"}
    params = {"title": "自动化任务报告", "desp": content}
    try:
        requests.post(url, json=params, headers=headers, timeout=10)
        mlog.info("Server 酱推送成功")
    except Exception as e:
        mlog.error(f"Server 酱推送失败: {e}")
