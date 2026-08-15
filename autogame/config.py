"""定义用户配置和应用路径，并负责读取配置文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML


class AppPaths(BaseModel):
    """集中定义项目源码之外的配置和运行数据路径。"""

    model_config = ConfigDict(frozen=True)

    root: Path

    @classmethod
    def default(cls) -> "AppPaths":
        """返回当前项目根目录对应的路径集合。"""

        return cls(root=Path(__file__).resolve().parent.parent)

    @property
    def config_file(self) -> Path:
        return self.root / "config.yaml"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def lock_dir(self) -> Path:
        return self.data_dir / "locks"

    @property
    def skyland_token_file(self) -> Path:
        return self.data_dir / "skyland_sign" / "token.txt"


class SystemConfig(BaseModel):
    """系统级运行配置。"""

    model_config = ConfigDict(extra="forbid")

    log_level: str = "INFO"
    automation_timeout_minutes: int = Field(default=90, gt=0, le=10080)
    completion_action: Literal["shutdown", "sleep", "none", "hibernate"] = "sleep"
    completion_action_delay_seconds: int = Field(default=60, ge=0, le=86400)
    server_chan_enabled: bool = True
    server_chan_key: str = ""


class TaskConfig(BaseModel):
    """单个自动化任务的用户配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_hours: float = Field(default=24.0, ge=0)
    script_path: str = ""


class Config(BaseModel):
    """项目完整配置。"""

    model_config = ConfigDict(extra="forbid")

    paths: AppPaths = Field(default_factory=AppPaths.default, exclude=True)
    system: SystemConfig = Field(default_factory=SystemConfig)
    tasks: dict[str, TaskConfig] = Field(default_factory=dict)

    @property
    def cfg_path(self) -> Path:
        return self.paths.config_file

    @property
    def log_dir(self) -> Path:
        return self.paths.log_dir

    @property
    def db_path(self) -> Path:
        return self.paths.state_file

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """从 YAML 文件读取并校验配置。"""

        paths = AppPaths(root=Path(path).resolve().parent) if path else AppPaths.default()
        config_path = Path(path) if path else paths.config_file
        if not config_path.exists():
            return cls(paths=paths)

        yaml_loader = YAML(typ="safe")
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml_loader.load(stream) or {}
        if not isinstance(raw, dict):
            raise ValueError("配置文件的顶层必须是对象")
        raw = dict(raw)
        raw["paths"] = paths
        return cls.model_validate(raw)
