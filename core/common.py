from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator
import yaml


class SystemConfig(BaseModel):
    log_level: str = "INFO"
    webhook_port: int = 8080
    shutdown_on_complete: bool = True
    shutdown_delay_seconds: int = 60
    shutdown_timeout_hours: float = 1.0
    server_chan_key: str = ""


class TaskConfig(BaseModel):
    enabled: bool = False
    interval_hours: float = 24.0
    # True = 任务完成须靠 webhook 回调通知，run() 只是触发动作
    # False = run() 返回即视为完成
    webhook_notify: bool = False


class Config(BaseModel):
    # root 和 cfg_path 不能更改
    root: Path = Path(__file__).resolve().parent.parent
    cfg_path: Path = root / "config.yaml"
    log_dir: Path = root / "logs"
    db_path: Path = root / "state.json"

    system: SystemConfig = SystemConfig()
    tasks: dict[str, TaskConfig] = {}

    @model_validator(mode="before")
    @classmethod
    def _coerce_tasks(cls, data: Any) -> Any:
        if isinstance(data, dict) and "tasks" in data:
            raw = data["tasks"]
            if isinstance(raw, dict):
                data["tasks"] = {
                    k: TaskConfig(**v) if isinstance(v, dict) else v
                    for k, v in raw.items()
                }
        return data

    @classmethod
    def load(cls) -> "Config":
        defaults = cls()
        if not defaults.cfg_path.exists():
            return defaults
        with open(defaults.cfg_path, mode="r", encoding="utf-8") as f:
            file_data = yaml.safe_load(f) or {}
        # 通过构造器重新走完整的验证流程
        return cls(**file_data)


cfg = Config.load()

if __name__ == "__main__":
    from rich import print
    print(cfg)
