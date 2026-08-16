"""实现启动脚本、读取日志和监控进程的通用适配器。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from autogame.logger import get_task_logger
from autogame.runtime.log_reader import IncrementalLogReader
from autogame.runtime.process import (
    ProcessHandle,
    process_is_running,
    start_process_async,
    stop_process_tree,
)
from autogame.tasks.base import AdapterResult, StartResult, TaskContext


CompletionMode = Literal["log_marker", "process_exit"]


@dataclass(frozen=True)
class ProcessScriptSpec:
    """描述一个外部脚本的固定进程和日志规则。"""

    process_name: str
    log_patterns: tuple[str, ...]
    completion_mode: CompletionMode
    startup_timeout_seconds: float = 15.0
    inactivity_completion_seconds: float | None = None
    timeout_seconds: float = 4 * 60 * 60


@dataclass(frozen=True)
class TaskLogLine:
    """表示一条已经清洗的任务业务日志。"""

    key: str
    message: str
    reportable: bool = True


@dataclass
class ProcessRun:
    """保存一次外部脚本运行所需的监控对象。"""

    script_process: ProcessHandle
    log_reader: IncrementalLogReader
    started_at_monotonic: float
    activity_seen: bool = False
    completion_started: bool = False
    completion_seen: bool = False
    failure_message: str | None = None
    last_meaningful_activity_at: float | None = None
    logged_lines: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class LogObservation:
    """表示日志解析器从一批新增行中观察到的结果。"""

    activity_seen: bool = False
    completion_started: bool = False
    completion_seen: bool = False
    failure_message: str | None = None
    messages: tuple[TaskLogLine, ...] = ()


class ProcessScriptAdapter:
    """外部脚本任务的通用生命周期实现。"""

    description = "等待脚本完成"
    requires_script = True

    def __init__(self, spec: ProcessScriptSpec) -> None:
        self.spec = spec

    async def start(self, context: TaskContext) -> StartResult:
        """建立日志读取基线并启动脚本。"""

        script_path = context.script_path
        task_log = get_task_logger(context.task_name)
        if script_path is None:
            return StartResult(None, AdapterResult("failed", "未配置脚本 exe 路径"))
        if not script_path.exists():
            return StartResult(None, AdapterResult("failed", f"脚本路径不存在：{script_path}"))

        try:
            log_reader = IncrementalLogReader(
                script_path.parent / "debug" / pattern
                for pattern in self.spec.log_patterns
            )
            log_reader.prime()

            script_process = await start_process_async(
                script_path,
                self.spec.process_name,
                self.spec.startup_timeout_seconds,
                allow_existing=False,
                restart_existing=True,
            )
        except Exception as exc:
            return StartResult(None, AdapterResult("failed", str(exc)))

        action = "已重启" if script_process.restarted else "已启动"
        task_log.info(
            "脚本进程 {}（PID {}）{}，开始监听任务日志",
            self.spec.process_name,
            script_process.pid,
            action,
        )
        run = ProcessRun(
            script_process=script_process,
            log_reader=log_reader,
            started_at_monotonic=time.monotonic(),
        )
        return StartResult(
            run,
            AdapterResult("running", waiting_for_completion=True),
        )

    async def poll(self, context: TaskContext, handle: object) -> AdapterResult:
        """读取增量日志并判断脚本是否完成。"""

        if not isinstance(handle, ProcessRun):
            return AdapterResult("failed", "任务监控句柄无效")

        task_log = get_task_logger(context.task_name)
        try:
            records = await asyncio.to_thread(handle.log_reader.read_lines)
            observation = self.observe_logs(records)
        except Exception as exc:
            return AdapterResult("failed", f"读取任务日志失败：{exc}")

        now = time.monotonic()
        handle.activity_seen |= observation.activity_seen
        handle.completion_started |= observation.completion_started
        handle.completion_seen |= observation.completion_seen
        if observation.activity_seen:
            handle.last_meaningful_activity_at = now
        if observation.failure_message:
            handle.failure_message = observation.failure_message

        report_lines: list[str] = []
        for item in observation.messages:
            if not item.message or item.key in handle.logged_lines:
                continue
            handle.logged_lines.add(item.key)
            task_log.info("{}", item.message)
            if item.reportable:
                report_lines.append(item.message)

        if handle.failure_message:
            return AdapterResult(
                "failed",
                handle.failure_message,
                report_lines=tuple(report_lines),
            )

        if handle.completion_seen:
            return AdapterResult(
                "completed",
                "检测到任务完成标志",
                report_lines=tuple(report_lines),
            )

        if not process_is_running(handle.script_process):
            if handle.completion_started:
                reason = "完成依据：结束进程任务启动后脚本退出"
                return AdapterResult(
                    "completed",
                    reason,
                    report_lines=(*report_lines, reason),
                )
            return self._on_process_exit(handle, report_lines)

        inactivity_seconds = self.spec.inactivity_completion_seconds
        if (
            inactivity_seconds is not None
            and handle.last_meaningful_activity_at is not None
            and now - handle.last_meaningful_activity_at >= inactivity_seconds
        ):
            minutes = inactivity_seconds / 60
            reason = f"完成依据：连续 {minutes:g} 分钟无有效业务日志"
            task_log.warning("{}，按静默规则认定完成", reason)
            await asyncio.to_thread(stop_process_tree, handle.script_process)
            return AdapterResult(
                "completed",
                reason,
                report_lines=(*report_lines, reason),
            )

        elapsed = now - handle.started_at_monotonic
        if elapsed >= self.spec.timeout_seconds:
            return AdapterResult(
                "failed",
                f"脚本运行超过 {self.spec.timeout_seconds / 60:.0f} 分钟",
                report_lines=tuple(report_lines),
            )

        return AdapterResult(
            "running",
            waiting_for_completion=True,
            report_lines=tuple(report_lines),
        )

    async def stop(self, context: TaskContext, handle: object) -> None:
        """超时或服务退出时停止本次启动的脚本进程。"""

        if isinstance(handle, ProcessRun):
            await asyncio.to_thread(stop_process_tree, handle.script_process)

    def observe_logs(self, records: list[tuple[Path, str]]) -> LogObservation:
        """默认只确认是否出现新的日志活动。"""

        return LogObservation(activity_seen=bool(records))

    def _on_process_exit(
        self,
        handle: ProcessRun,
        report_lines: list[str],
    ) -> AdapterResult:
        """根据任务类型判断脚本退出后的最终结果。"""

        if self.spec.completion_mode == "log_marker":
            return AdapterResult(
                "failed",
                "脚本已退出，但未检测到完成标志",
                report_lines=tuple(report_lines),
            )
        if handle.activity_seen:
            return AdapterResult(
                "completed",
                "检测到脚本正常结束",
                report_lines=tuple(report_lines),
            )
        return AdapterResult(
            "failed",
            "脚本过早退出，未检测到有效任务日志",
            report_lines=tuple(report_lines),
        )
