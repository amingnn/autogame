import logging
import logging.config
import logging.handlers
import threading
import requests
from core.common import cfg


from datetime import datetime

cfg.log_dir.mkdir(parents=True, exist_ok=True)
log_file = cfg.log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "standard",
            "filename": str(log_file),
            "maxBytes": 10485760,
            "backupCount": 5,
            "encoding": "utf8",
        },
    },
    "loggers": {
        "": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
        },
    },
}


def _log_header(log: logging.Logger) -> None:
    width = 40
    log.info("=" * width)
    log.info(f"{'Auto Game Report':^{width}}")
    log.info(f"{'RUNNING':^{width}}")
    log.info("=" * width)


def init_app_logging() -> logging.Logger:
    logging.config.dictConfig(LOGGING_CONFIG)
    logger = logging.getLogger("AutoGame")
    _log_header(logger)
    return logger


mlog: logging.Logger = init_app_logging()

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

def log_wrapper(content: str, title: str | None = None) -> str:
    """生成带标题分隔线的纯文本段落，对应 Server酱推送样式。"""
    frt = "-" * 10
    header = f"{frt}{title}{frt}" if title else frt * 3
    footer = frt * 3
    return f"{header}\n{content.strip()}\n{footer}"


def push_wechat(send_key: str) -> None:
    """将本次会话收集到的任务报告推送到 Server 酱。"""
    if not _report:
        mlog.warning("推送内容为空，跳过")
        return
    content = "\n\n".join(_report)
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    headers = {"Content-Type": "application/json;charset=utf-8"}
    params = {"title": "自动化任务报告", "desp": content}
    try:
        requests.post(url, json=params, headers=headers, timeout=10)
        mlog.info("Server 酱推送成功")
    except Exception as e:
        mlog.error(f"Server 酱推送失败: {e}")
