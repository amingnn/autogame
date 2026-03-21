import logging
import logging.config
import logging.handlers
import requests
from datetime import datetime
from core.common import cfg


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


def time_wrapper(message: str) -> str:
    timestamp = datetime.now().strftime("[%H:%M:%S] ")
    return f"{timestamp}{message}"


def log_wrapper(content: str, title: str | None = None) -> str:
    frt = "-" * 10
    title = title if title else frt
    return (
        f"```text\n"
        f"{frt}{title}{frt}\n"
        f"{content.strip()}\n"
        f"{frt}{frt}{frt}\n"
        f"```\n"
    )


def push_wechat(send_key: str) -> None:
    """将当日日志文件内容推送到 Server 酱。"""
    if not log_file.exists():
        mlog.warning("日志文件不存在，跳过推送")
        return
    with open(log_file, mode="r", encoding="utf-8") as f:
        content = f.read()
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    headers = {"Content-Type": "application/json;charset=utf-8"}
    params = {"title": "自动化任务报告", "desp": content}
    try:
        requests.post(url, json=params, headers=headers, timeout=10)
        mlog.info("Server 酱推送成功")
    except Exception as e:
        mlog.error(f"Server 酱推送失败: {e}")

if __name__ == '__main__':
    mlog.info("脚本正式开始执行...")
