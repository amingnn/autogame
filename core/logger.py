"""统一配置 Loguru 日志，并清理七天以前的日志文件。"""

from __future__ import annotations

import inspect
import logging
import sys
import time
from pathlib import Path

from loguru import logger


_configured = False


class _StandardLogHandler(logging.Handler):
    """把 Uvicorn 等标准库日志转发给 Loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = inspect.currentframe()
        depth = 1
        while frame and frame.f_code.co_filename == __file__:
            frame = frame.f_back
            depth += 1
        logger.bind(component=record.name).opt(exception=record.exc_info, depth=depth).log(
            level,
            record.getMessage(),
        )


def _cleanup_old_logs(log_dir: Path) -> None:
    """删除日志目录中修改时间超过七天的日志文件。"""

    cutoff = time.time() - 7 * 24 * 60 * 60
    for path in log_dir.glob("*.log"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            # 日志文件被其他程序占用时，不阻止应用启动。
            continue


def _configure_standard_logging() -> None:
    """让第三方标准库日志进入 Loguru。"""

    handler = _StandardLogHandler()
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)

    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "urllib3",
        "httpcore",
        "httpx",
        "requests",
    ):
        external = logging.getLogger(name)
        external.handlers = [handler]
        external.setLevel(logging.WARNING)
        external.propagate = False


def configure_logging(log_dir: Path, level: str = "INFO", force: bool = False) -> None:
    """配置控制台、主日志和通知日志。"""

    global _configured
    if _configured and not force:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(log_dir)
    logger.remove()

    common_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
        "{extra[component]} | {message}"
    )
    normal_filter = lambda record: record["extra"].get("channel") != "notify"
    notify_filter = lambda record: record["extra"].get("channel") == "notify"

    logger.add(
        sys.stdout,
        level=level.upper(),
        format=common_format,
        filter=normal_filter,
        enqueue=True,
        colorize=False,
    )
    logger.add(
        log_dir / "{time:YYYY-MM-DD}.log",
        level=level.upper(),
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        format=common_format,
        filter=normal_filter,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    logger.add(
        log_dir / "notify-{time:YYYY-MM-DD}.log",
        level="INFO",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
        filter=notify_filter,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _configure_standard_logging()
    _configured = True


def initialize_default_logging() -> None:
    """在其他模块导入日志对象前创建默认日志配置。"""

    from core.common import Config

    default = Config()
    configure_logging(default.log_dir, default.system.log_level)


initialize_default_logging()

mlog = logger.bind(component="autogame")
notify_logger = logger.bind(component="notify", channel="notify")
