"""以加锁和原子替换方式保存任务最近成功时间。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from filelock import FileLock


class StateStore:
    """管理任务成功时间文件。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = FileLock(str(path) + ".lock")

    def load(self) -> dict[str, str]:
        """读取有效的任务成功时间字典。"""

        if not self.path.exists():
            return {}
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(name): str(value)
            for name, value in data.items()
            if isinstance(value, str)
        }

    def record_success(self, task_name: str, completed_at: datetime) -> None:
        """合并并原子保存一个任务的成功时间。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            state: dict[str, str] = {}
            if self.path.exists():
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(current, dict):
                        state = {
                            str(name): str(value)
                            for name, value in current.items()
                            if isinstance(value, str)
                        }
                except (OSError, json.JSONDecodeError):
                    state = {}
            state[task_name] = completed_at.isoformat()
            self._write_atomic(state)

    def _write_atomic(self, state: dict[str, str]) -> None:
        """写入同目录临时文件后替换正式状态文件。"""

        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f"{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if temp_name:
                temp_path = Path(temp_name)
                if temp_path.exists():
                    temp_path.unlink()
