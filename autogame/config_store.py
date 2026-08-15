"""提供 config.yaml 的安全编辑、备份和重载能力。"""

from __future__ import annotations

import copy
import hashlib
import io
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from filelock import FileLock
from ruamel.yaml import YAML

from autogame.config import Config, SystemConfig, TaskConfig


class ConfigConflictError(RuntimeError):
    """表示页面使用的配置版本已经过期。"""


def _to_plain(value: Any) -> Any:
    """把 ruamel.yaml 对象转换为普通 Python 对象。"""

    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


class ConfigStore:
    """以原子方式修改正式配置文件。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = FileLock(str(path) + ".lock")
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._yaml.default_flow_style = False

    def revision(self) -> str:
        """返回配置文件当前内容的 SHA-256 版本号。"""

        if not self.path.exists():
            return ""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def load(self) -> Config:
        """读取并校验当前配置。"""

        return Config.load(self.path)

    def update_task(
        self,
        task_name: str,
        patch: dict[str, Any],
        expected_revision: str | None = None,
    ) -> Config:
        """校验并原子更新指定任务配置。"""

        allowed = {"enabled", "interval_hours", "script_path"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"不允许修改的配置字段：{sorted(unknown)}")
        if not patch:
            raise ValueError("至少需要一个配置字段")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._validate_revision(expected_revision)
            data = self._read_data()
            tasks = data.setdefault("tasks", {})
            if not isinstance(tasks, Mapping):
                raise ValueError("配置文件中的 tasks 必须是对象")
            current = tasks.setdefault(task_name, {})
            if not isinstance(current, Mapping):
                current = {}
                tasks[task_name] = current
            merged = _to_plain(copy.deepcopy(current))
            merged.update(patch)
            TaskConfig.model_validate(merged)
            for key, value in patch.items():
                current[key] = value
            Config.model_validate(_to_plain(data))
            self._backup_and_write(data)
        return self.load()

    def update_system(
        self,
        patch: dict[str, Any],
        expected_revision: str | None = None,
    ) -> Config:
        """校验并原子更新全局运行配置。"""

        allowed = {
            "log_level",
            "automation_timeout_minutes",
            "completion_action",
            "completion_action_delay_seconds",
            "server_chan_enabled",
            "server_chan_key",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"不允许修改的全局配置字段：{sorted(unknown)}")
        if not patch:
            raise ValueError("至少需要一个全局配置字段")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._validate_revision(expected_revision)
            data = self._read_data()
            system = data.setdefault("system", {})
            if not isinstance(system, Mapping):
                raise ValueError("配置文件中的 system 必须是对象")
            merged = _to_plain(copy.deepcopy(system))
            merged.update(patch)
            SystemConfig.model_validate(merged)
            for key, value in patch.items():
                system[key] = value
            Config.model_validate(_to_plain(data))
            self._backup_and_write(data)
        return self.load()

    def _validate_revision(self, expected_revision: str | None) -> None:
        """校验页面提交时携带的配置版本。"""

        if expected_revision and expected_revision != self.revision():
            raise ConfigConflictError("配置文件已经被其他操作修改，请重新加载")

    def _read_data(self) -> Any:
        """读取 ruamel YAML 数据。"""

        if not self.path.exists():
            return {}
        data = self._yaml.load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise ValueError("配置文件的顶层必须是对象")
        return data

    def _backup_and_write(self, data: Any) -> None:
        """创建备份并原子写入配置。"""

        old_bytes = self.path.read_bytes() if self.path.exists() else b""
        if old_bytes:
            self.path.with_name(self.path.name + ".bak").write_bytes(old_bytes)
        self._atomic_dump(data)

    def _atomic_dump(self, data: Any) -> None:
        """把 YAML 数据写入临时文件后原子替换正式文件。"""

        buffer = io.StringIO()
        self._yaml.dump(data, buffer)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(buffer.getvalue(), encoding="utf-8")
        os.replace(temporary, self.path)
