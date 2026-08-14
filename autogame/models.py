"""定义任务运行时状态和状态序列化结构。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


TaskState = Literal[
    "disabled",
    "cooldown",
    "pending",
    "starting",
    "running",
    "completed",
    "failed",
    "timed_out",
]


def format_datetime(value: datetime | None) -> str | None:
    """把时间转换为带 UTC 时区的 ISO 8601 字符串。"""

    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


@dataclass
class TaskRuntime:
    """保存一个任务的临时运行状态。"""

    name: str
    state: TaskState
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    waiting_for_completion: bool = False

    def elapsed_seconds(self, now: datetime | None = None) -> float | None:
        """返回任务从开始到当前或完成时的秒数。"""

        if self.started_at is None:
            return None
        now = now or datetime.now(tz=timezone.utc)
        end = self.finished_at or now
        return max(0.0, (end - self.started_at).total_seconds())

    def as_dict(self) -> dict[str, object]:
        """返回供接口和桌面页面使用的字典。"""

        return {
            "name": self.name,
            "state": self.state,
            "started_at": format_datetime(self.started_at),
            "finished_at": format_datetime(self.finished_at),
            "last_success_at": format_datetime(self.last_success_at),
            "last_error": self.last_error,
            "waiting_for_completion": self.waiting_for_completion,
        }
