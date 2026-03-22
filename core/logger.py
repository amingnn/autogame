import logging
import logging.config
import logging.handlers
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
