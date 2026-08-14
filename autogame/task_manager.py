"""统一管理任务启动、监控、状态和配置热更新。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from autogame.config import Config
from autogame.config_store import ConfigStore
from autogame.logger import configure_logging, get_task_logger, mlog
from autogame.models import TaskRuntime, TaskState, format_datetime
from autogame.registry import get_task_definition
from autogame.runtime.execution_lock import ExecutionLease, ExecutionLock
from autogame.runtime.state_store import StateStore
from autogame.tasks.base import AdapterResult, TaskAdapter, TaskContext


TERMINAL_STATES: set[TaskState] = {
    "disabled",
    "cooldown",
    "completed",
    "failed",
    "timed_out",
}


@dataclass
class ActiveTask:
    """保存一次活动任务的适配器、监控句柄和跨进程锁。"""

    adapter: TaskAdapter
    context: TaskContext
    handle: object
    lease: ExecutionLease


class TaskManager:
    """桌面和自动化模式共享的任务生命周期核心。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._config_store = ConfigStore(config.cfg_path)
        self._state_store = StateStore(config.db_path)
        self._execution_lock = ExecutionLock(config.paths.lock_dir)
        self._state = self._state_store.load()
        self._runtime: dict[str, TaskRuntime] = {}
        self._active: dict[str, ActiveTask] = {}
        self._task_report_lines: dict[str, list[str]] = {}
        self._started_at = datetime.now(tz=timezone.utc)
        self._monitor_interval_seconds = 2.0
        self._initialize_runtime_state()

    @property
    def active_task_names(self) -> set[str]:
        """返回当前进程正在管理的任务名称。"""

        return set(self._active)

    def get_status_snapshot(self) -> dict[str, object]:
        """生成供桌面页面使用的完整状态。"""

        now = datetime.now(tz=timezone.utc)
        tasks: list[dict[str, object]] = []
        enabled_names = [
            name for name, task in self.config.tasks.items() if task.enabled
        ]
        completed = 0
        for name, task_config in self.config.tasks.items():
            runtime = self._runtime[name]
            if task_config.enabled and runtime.state in {"completed", "cooldown"}:
                completed += 1
            definition = get_task_definition(name)
            next_eligible_at = None
            if runtime.last_success_at is not None:
                next_eligible_at = datetime.fromtimestamp(
                    runtime.last_success_at.timestamp()
                    + task_config.interval_hours * 3600,
                    tz=timezone.utc,
                ).isoformat()

            item = runtime.as_dict()
            item.update(
                {
                    "enabled": task_config.enabled,
                    "interval_hours": task_config.interval_hours,
                    "script_path": task_config.script_path,
                    "task_type": definition.name if definition else None,
                    "requires_script": definition.requires_script if definition else False,
                    "completion_description": (
                        definition.description if definition else "任务未注册"
                    ),
                    "elapsed_seconds": runtime.elapsed_seconds(now),
                    "next_eligible_at": next_eligible_at,
                }
            )
            tasks.append(item)

        total = len(enabled_names)
        return {
            "status": "running" if self._active else "idle",
            "generated_at": format_datetime(now),
            "started_at": format_datetime(self._started_at),
            "config_revision": self._config_store.revision(),
            "progress": {
                "completed": completed,
                "total": total,
                "percent": 100.0 if total == 0 else round(completed / total * 100, 1),
            },
            "system": self._safe_system_config(),
            "tasks": tasks,
        }

    def update_task_config(
        self,
        task_name: str,
        patch: dict[str, object],
        expected_revision: str | None = None,
    ) -> dict[str, object]:
        """保存任务配置并立即应用。"""

        if task_name not in self.config.tasks:
            raise KeyError(task_name)
        config = self._config_store.update_task(
            task_name,
            patch,
            expected_revision=expected_revision,
        )
        self._apply_config(config)
        return self._find_task_snapshot(task_name)

    def update_system_config(
        self,
        patch: dict[str, object],
        expected_revision: str | None = None,
    ) -> dict[str, object]:
        """保存系统配置并应用新的日志级别。"""

        config = self._config_store.update_system(
            patch,
            expected_revision=expected_revision,
        )
        self._apply_config(config)
        configure_logging(config.log_dir, config.system.log_level, force=True)
        return self._safe_system_config()

    def reload_config(self) -> dict[str, object]:
        """重新读取磁盘上的正式配置。"""

        self._apply_config(self._config_store.load())
        return self.get_status_snapshot()

    def should_run(self, task_name: str) -> bool:
        """判断任务是否启用且已超过间隔冷却。"""

        task_config = self.config.tasks.get(task_name)
        return bool(task_config and task_config.enabled and self._should_run_raw(task_name))

    def get_task_state(self, task_name: str) -> TaskState | None:
        """返回一个任务的当前状态。"""

        runtime = self._runtime.get(task_name)
        return runtime.state if runtime else None

    def get_task_result(self, task_name: str) -> dict[str, object]:
        """返回自动化报告需要的任务结果。"""

        runtime = self._runtime[task_name]
        return {
            "name": task_name,
            "state": runtime.state,
            "elapsed_seconds": runtime.elapsed_seconds() or 0,
            "error": runtime.last_error,
            "lines": list(self._task_report_lines.get(task_name, ())),
        }

    async def run_task(self, task_name: str, force: bool = False) -> bool:
        """通过注册任务启动一次运行；强制运行可跳过冷却。"""

        task_config = self.config.tasks.get(task_name)
        definition = get_task_definition(task_name)
        if task_config is None:
            mlog.error("无法启动未注册任务：{}", task_name)
            return False
        if definition is None:
            self._set_runtime_state(
                task_name,
                "failed",
                error="任务未在 registry.py 中注册",
            )
            mlog.error("无法启动未注册任务：{}", task_name)
            return False
        if not task_config.enabled:
            self._set_runtime_state(task_name, "disabled")
            return False
        if task_name in self._active:
            get_task_logger(task_name).warning("已经在当前进程运行，忽略重复触发")
            return False
        if not force and not self.should_run(task_name):
            self._set_runtime_state(task_name, "cooldown")
            get_task_logger(task_name).debug("仍在冷却中，跳过执行")
            return False

        lease = self._execution_lock.acquire(f"task-{task_name}")
        if lease is None:
            self._set_runtime_state(
                task_name,
                "failed",
                error="另一个 AutoGame 进程正在运行此任务",
            )
            return False

        runtime = self._runtime[task_name]
        runtime.started_at = datetime.now(tz=timezone.utc)
        runtime.finished_at = None
        runtime.last_error = None
        runtime.waiting_for_completion = False
        self._task_report_lines[task_name] = []
        self._set_runtime_state(task_name, "starting")
        task_log = get_task_logger(task_name)
        task_log.info("任务开始，任务类型：{}", definition.name)

        context = TaskContext(
            task_name=task_name,
            config=task_config,
            started_at=runtime.started_at,
        )
        adapter = definition.task_factory(self.config)
        try:
            start_result = await adapter.start(context)
            outcome = start_result.result
            self._record_outcome(task_name, outcome)
            if outcome.state == "failed":
                runtime.last_error = outcome.message or "任务启动失败"
                await self._finish_task(task_name, lease=lease, success=False)
                return False
            if outcome.state == "completed":
                await self._finish_task(task_name, lease=lease, success=True)
                return True
            if start_result.handle is None:
                runtime.last_error = "任务未返回监控句柄"
                await self._finish_task(task_name, lease=lease, success=False)
                return False

            self._active[task_name] = ActiveTask(
                adapter=adapter,
                context=context,
                handle=start_result.handle,
                lease=lease,
            )
            runtime.waiting_for_completion = outcome.waiting_for_completion
            self._set_runtime_state(task_name, "running")
            task_log.info("已进入运行中，等待任务完成")
            return True
        except Exception as exc:
            runtime.last_error = str(exc)
            task_log.exception("任务启动异常")
            await self._finish_task(task_name, lease=lease, success=False)
            return False

    async def poll_active_tasks(self) -> None:
        """轮询一次所有活动任务。"""

        for task_name, active in list(self._active.items()):
            try:
                outcome = await active.adapter.poll(active.context, active.handle)
            except Exception as exc:
                outcome = AdapterResult("failed", f"任务监控异常：{exc}")

            self._record_outcome(task_name, outcome)

            if outcome.state == "running":
                self._runtime[task_name].waiting_for_completion = (
                    outcome.waiting_for_completion
                )
                continue
            if outcome.state == "failed":
                self._runtime[task_name].last_error = outcome.message or "任务执行失败"
                await self._finish_task(task_name, success=False)
            else:
                await self._finish_task(task_name, success=True)

    async def monitor_loop(self, stop_event: asyncio.Event) -> None:
        """持续监控活动任务，直到入口层请求停止。"""

        while not stop_event.is_set():
            await self.poll_active_tasks()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._monitor_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def wait_for_tasks(
        self,
        task_names: set[str],
        timeout_seconds: float,
    ) -> bool:
        """等待指定任务全部进入终态，超时返回 ``False``。"""

        if not task_names:
            return True

        async def wait() -> None:
            while True:
                if all(
                    self.get_task_state(name) in TERMINAL_STATES
                    for name in task_names
                ):
                    return
                await asyncio.sleep(0.2)

        try:
            await asyncio.wait_for(wait(), timeout=timeout_seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def timeout_tasks(self, task_names: set[str]) -> None:
        """停止并标记指定集合中仍未结束的任务。"""

        for task_name in task_names:
            if task_name in self._active:
                active = self._active[task_name]
                await active.adapter.stop(active.context, active.handle)
                await self._finish_task(
                    task_name,
                    success=False,
                    final_state="timed_out",
                    error="自动化会话超过全局超时时间",
                )

    async def shutdown(self) -> None:
        """停止由当前进程启动的活动任务并释放执行锁。"""

        for task_name, active in list(self._active.items()):
            await active.adapter.stop(active.context, active.handle)
            await self._finish_task(
                task_name,
                success=False,
                error="AutoGame 已停止",
            )

    async def _finish_task(
        self,
        task_name: str,
        success: bool,
        lease: ExecutionLease | None = None,
        final_state: TaskState | None = None,
        error: str | None = None,
    ) -> None:
        """统一完成任务、保存成功时间并释放跨进程锁。"""

        runtime = self._runtime.get(task_name)
        if runtime is None:
            if lease:
                lease.release()
            return

        active = self._active.pop(task_name, None)
        effective_lease = lease or (active.lease if active else None)
        runtime.finished_at = datetime.now(tz=timezone.utc)
        runtime.waiting_for_completion = False

        if success:
            completed_at = runtime.finished_at
            self._state_store.record_success(task_name, completed_at)
            self._state[task_name] = completed_at.isoformat()
            runtime.last_success_at = completed_at
            runtime.last_error = None
            self._set_runtime_state(task_name, "completed")
            get_task_logger(task_name).info(
                "任务完成，耗时 {:.1f} 秒", runtime.elapsed_seconds() or 0
            )
        else:
            runtime.last_error = error or runtime.last_error or "任务执行失败"
            self._set_runtime_state(
                task_name,
                final_state or "failed",
                error=runtime.last_error,
            )
            get_task_logger(task_name).warning("任务失败：{}", runtime.last_error)

        if effective_lease:
            effective_lease.release()

    def _initialize_runtime_state(self) -> None:
        """根据配置和历史成功时间初始化状态。"""

        for name, task_config in self.config.tasks.items():
            last_success = self._parse_time(self._state.get(name))
            if not task_config.enabled:
                state: TaskState = "disabled"
            elif self._should_run_raw(name):
                state = "pending"
            else:
                state = "cooldown"
            self._runtime[name] = TaskRuntime(
                name=name,
                state=state,
                last_success_at=last_success,
            )
            self._task_report_lines.setdefault(name, [])

    def _apply_config(self, config: Config) -> None:
        """应用新配置，同时保留正在运行任务的状态。"""

        previous_runtime = self._runtime
        self.config = config
        self._runtime = {}
        for name, task_config in config.tasks.items():
            runtime = previous_runtime.get(name) or TaskRuntime(name=name, state="pending")
            self._runtime[name] = runtime
            self._task_report_lines.setdefault(name, [])
            if name in self._active:
                continue
            if not task_config.enabled:
                runtime.state = "disabled"
            elif runtime.state not in {"completed", "failed", "timed_out"}:
                runtime.state = "pending" if self._should_run_raw(name) else "cooldown"

    def _should_run_raw(self, task_name: str) -> bool:
        """仅按最近成功时间和间隔判断是否到期。"""

        task = self.config.tasks.get(task_name)
        if task is None:
            return False
        last_success = self._parse_time(self._state.get(task_name))
        if last_success is None:
            return True
        elapsed = datetime.now(tz=timezone.utc) - last_success
        return elapsed.total_seconds() >= task.interval_hours * 3600

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        """解析状态文件中的 ISO 时间。"""

        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _set_runtime_state(
        self,
        task_name: str,
        state: TaskState,
        error: str | None = None,
    ) -> None:
        """修改任务状态并同步错误信息。"""

        runtime = self._runtime[task_name]
        runtime.state = state
        if error is not None:
            runtime.last_error = error

    def _find_task_snapshot(self, task_name: str) -> dict[str, object]:
        """从完整快照中返回指定任务。"""

        for task in self.get_status_snapshot()["tasks"]:
            if task["name"] == task_name:
                return task
        raise KeyError(task_name)

    def _safe_system_config(self) -> dict[str, object]:
        """返回不包含 SendKey 原文的系统配置。"""

        system = self.config.system.model_dump()
        configured = bool(system.pop("server_chan_key", ""))
        system["server_chan_key_configured"] = configured
        return system

    def _record_outcome(self, task_name: str, outcome: AdapterResult) -> None:
        """收集适配器提供的可推送业务结果并去重。"""

        target = self._task_report_lines.setdefault(task_name, [])
        for line in outcome.report_lines:
            if line and line not in target:
                target.append(line)
