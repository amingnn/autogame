"""定义项目配置模型，并负责读取配置文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


def _project_root() -> Path:
    """返回项目根目录。"""

    return Path(__file__).resolve().parent.parent


class SystemConfig(BaseModel):
    """系统级运行配置。"""

    log_level: str = "INFO"
    webhook_port: int = Field(default=8000, ge=1, le=65535)
    shutdown_on_complete: bool = True
    shutdown_delay_seconds: int = Field(default=60, ge=0, le=86400)
    shutdown_timeout_hours: float = Field(default=1.5, gt=0, le=168)
    completion_action: Literal["shutdown", "sleep", "none", "hibernate"] = "sleep"
    server_chan_key: str = ""


class LauncherConfig(BaseModel):
    """任务启动方式配置。"""

    type: Literal["none", "application"] = "none"
    path: str = ""
    process_name: str = ""
    startup_timeout_seconds: float = Field(default=15.0, ge=1, le=300)
    restart_existing: bool = True

    @model_validator(mode="after")
    def _校验应用启动配置(self) -> "LauncherConfig":
        """应用启动方式必须同时提供路径和进程名。"""

        if self.type == "application" and (not self.path or not self.process_name):
            raise ValueError("application 启动方式必须提供 path 和 process_name")
        return self


class TaskConfig(BaseModel):
    """单个自动化任务的配置。"""

    enabled: bool = False
    interval_hours: float = Field(default=24.0, ge=0)
    launcher: LauncherConfig = Field(default_factory=LauncherConfig)


class Config(BaseModel):
    """项目完整配置。"""

    root: Path = Field(default_factory=_project_root)
    cfg_path: Path = Field(default_factory=lambda: _project_root() / "config.yaml")
    log_dir: Path = Field(default_factory=lambda: _project_root() / "logs")
    db_path: Path = Field(default_factory=lambda: _project_root() / "state.json")
    system: SystemConfig = Field(default_factory=SystemConfig)
    tasks: dict[str, TaskConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """从 YAML 文件读取并校验配置。"""

        default = cls()
        config_path = Path(path) if path else default.cfg_path
        if not config_path.exists():
            return default.model_copy(update={"cfg_path": config_path})

        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        if not isinstance(raw, dict):
            raise ValueError("配置文件的顶层必须是对象")
        raw = dict(raw)
        raw["cfg_path"] = config_path
        return cls.model_validate(raw)


cfg = Config.load()
