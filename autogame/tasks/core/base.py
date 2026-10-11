"""定义所有任务共享的生命周期接口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from autogame.config import TaskConfig


AdapterState = Literal["running", "completed", "failed"]


@dataclass(frozen=True)
class TaskContext:
    """描述一次任务运行上下文。"""

    task_name: str
    config: TaskConfig
    started_at: datetime

    @property
    def script_path(self) -> Path | None:
        """返回用户为任务选择的脚本路径。"""

        if not self.config.script_path:
            return None
        return Path(self.config.script_path)


@dataclass(frozen=True)
class AdapterResult:
    """表示适配器当前一次检查的结果。"""

    state: AdapterState
    message: str | None = None
    waiting_for_completion: bool = False
    report_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class StartResult:
    """表示任务启动后的句柄和初始结果。"""

    handle: object | None
    result: AdapterResult


class TaskAdapter(Protocol):
    """任务适配器必须提供的生命周期方法。"""

    description: str
    requires_script: bool

    async def start(self, context: TaskContext) -> StartResult:
        """启动任务并返回后续监控所需的句柄。"""

    async def poll(self, context: TaskContext, handle: object) -> AdapterResult:
        """检查任务是否仍在运行或已经完成。"""

    async def stop(self, context: TaskContext, handle: object) -> None:
        """停止由本次任务启动并且仍在运行的进程。"""
