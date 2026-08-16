"""定义用户配置和应用路径，并负责读取配置文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from ruamel.yaml import YAML


class AppPaths(BaseModel):
    """集中定义项目源码之外的配置和运行数据路径。"""

    model_config = ConfigDict(frozen=True)

    root: Path
    config_file_override: Path | None = Field(default=None, exclude=True)

    @classmethod
    def default(cls) -> "AppPaths":
        """返回当前项目根目录对应的路径集合。"""

        return cls(root=Path(__file__).resolve().parent.parent)

    @property
    def config_file(self) -> Path:
        return self.config_file_override or self.root / "config.yaml"

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


class SkylandAccountConfig(BaseModel):
    """森空岛手机号登录配置。"""

    model_config = ConfigDict(extra="forbid")

    phone: str = ""
    password: SecretStr = Field(default_factory=lambda: SecretStr(""))

    @property
    def has_any_value(self) -> bool:
        """返回是否填写过手机号或密码。"""

        return bool(self.phone.strip() or self.password.get_secret_value())

    @property
    def is_complete(self) -> bool:
        """返回手机号和密码是否均已填写。"""

        return bool(self.phone.strip() and self.password.get_secret_value())


class TaskConfig(BaseModel):
    """单个自动化任务的用户配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_hours: float = Field(default=24.0, ge=0)
    script_path: str = ""
    account: SkylandAccountConfig | None = None


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

        if path:
            config_path = Path(path).resolve()
            root = (
                config_path.parent.parent
                if config_path.parent.name.lower() == "data"
                else config_path.parent
            )
            paths = AppPaths(
                root=root,
                config_file_override=config_path,
            )
        else:
            paths = AppPaths.default()
            config_path = paths.config_file
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
