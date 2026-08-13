"""任务调度、类型化执行和运行时状态管理。"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import time
from datetime import datetime, timezone

from core.common import Config
from core.config_store import ConfigStore
from core.launcher import 启动并验证
from core.logger import mlog
from core.notify import push_wechat, report
from core.status import TaskRuntime, TaskState, 转换时间
from core.task_registry import get_task_definition


class Scheduler:
    """任务调度核心，也是桌面页面获取状态的唯一来源。"""

    def __init__(
        self,
        config: Config,
        auto_shutdown: bool = True,
        auto_schedule: bool = False,
    ) -> None:
        self.config = config
        self.auto_shutdown = auto_shutdown
        self.auto_schedule = auto_schedule
        self._config_store = ConfigStore(config.cfg_path)
        self._state: dict[str, str] = {}
        self._runtime: dict[str, TaskRuntime] = {}
        self._session_done: dict[str, bool] = {}
        self._active_tasks: set[str] = set()
        self._shutdown_triggered = False
        self._stop_requested = False
        self._stop_event: asyncio.Event | None = None
        self._start_time = datetime.now(tz=timezone.utc)
        self._poll_interval_seconds = 30

        self._load_state()
        self._initialize_runtime_state()
        self._log_initial_status()

    @property
    def stop_requested(self) -> bool:
        """返回调度器是否请求主程序退出。"""

        return self._stop_requested

    def bind_stop_event(self, stop_event: asyncio.Event) -> None:
        """绑定主程序的异步退出事件。"""

        self._stop_event = stop_event

    def get_status_snapshot(self) -> dict[str, object]:
        """生成供桌面页面和内部接口使用的完整状态。"""

        now = datetime.now(tz=timezone.utc)
        enabled_names = [
            name for name, task in self.config.tasks.items() if task.enabled
        ]
        completed = sum(self._session_done.get(name, False) for name in enabled_names)
        total = len(enabled_names)
        percent = 100.0 if total == 0 else round(completed / total * 100, 1)

        if self._stop_requested:
            service_status = "stopping"
        elif self._active_tasks:
            service_status = "running"
        elif self.auto_schedule and self._shutdown_triggered:
            service_status = "completed"
        elif self.auto_schedule:
            service_status = "ready"
        else:
            service_status = "idle"

        tasks: list[dict[str, object]] = []
        for name, task_config in self.config.tasks.items():
            runtime = self._runtime[name]
            next_eligible_at = None
            if runtime.last_success_at is not None:
                next_eligible_at = runtime.last_success_at.timestamp() + (
                    task_config.interval_hours * 3600
                )

            definition = get_task_definition(name)
            item = runtime.as_dict()
            item.update(
                {
                    "enabled": task_config.enabled,
                    "interval_hours": task_config.interval_hours,
                    "launcher": task_config.launcher.model_dump(),
                    "completion_signal": definition.completion_signal,
                    "completion_description": self._completion_description(
                        definition.completion_signal
                    ),
                    "elapsed_seconds": runtime.elapsed_seconds(now),
                    "next_eligible_at": (
                        datetime.fromtimestamp(
                            next_eligible_at,
                            tz=timezone.utc,
                        ).isoformat()
                        if next_eligible_at is not None
                        else None
                    ),
                }
            )
            tasks.append(item)

        return {
            "status": service_status,
            "generated_at": 转换时间(now),
            "started_at": 转换时间(self._start_time),
            "shutdown_requested": self._stop_requested,
            "config_revision": self._config_store.revision(),
            "auto_schedule": self.auto_schedule,
            "progress": {
                "completed": completed,
                "total": total,
                "percent": percent,
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
        """保存任务配置并让当前调度器重新加载配置。"""

        if task_name not in self.config.tasks:
            raise KeyError(task_name)
        new_config = self._config_store.update_task(
            task_name,
            patch,
            expected_revision=expected_revision,
        )
        self._apply_config(new_config)
        return self._find_task_snapshot(task_name)

    def update_system_config(
        self,
        patch: dict[str, object],
        expected_revision: str | None = None,
    ) -> dict[str, object]:
        """保存全局配置并立即应用可热更新的字段。"""

        new_config = self._config_store.update_system(
            patch,
            expected_revision=expected_revision,
        )
        old_port = self.config.system.webhook_port
        self._apply_config(new_config)
        if old_port != new_config.system.webhook_port:
            mlog.warning("Webhook 端口已保存为 {}，重启桌面或自动化进程后生效", new_config.system.webhook_port)
        from core.logger import configure_logging

        configure_logging(
            new_config.log_dir,
            new_config.system.log_level,
            force=True,
        )
        return self._safe_system_config()

    def reload_config(self) -> dict[str, object]:
        """重新读取磁盘上的 config.yaml。"""

        self._apply_config(self._config_store.load())
        return self.get_status_snapshot()

    def should_run(self, task_name: str) -> bool:
        """判断任务是否启用且已经超过冷却时间。"""

        task_config = self.config.tasks.get(task_name)
        if not task_config or not task_config.enabled:
            return False
        return self._should_run_raw(task_name)

    def mark_done(self, task_name: str, success: bool = True) -> bool:
        """把正在运行的任务标记为完成或失败。"""

        runtime = self._runtime.get(task_name)
        if runtime is None or task_name not in self._session_done:
            mlog.warning("[{}] 忽略不属于当前会话的完成回调", task_name)
            return False
        if runtime.state not in {"starting", "running"}:
            mlog.warning("[{}] 忽略重复完成回调，当前状态：{}", task_name, runtime.state)
            return False

        runtime.finished_at = datetime.now(tz=timezone.utc)
        runtime.waiting_for_callback = False
        self._active_tasks.discard(task_name)
        self._session_done[task_name] = True
        if success:
            runtime.last_success_at = self._record_run(task_name)
            runtime.last_error = None
            self._set_runtime_state(task_name, "completed")
            mlog.info(
                "<<< [{}] 任务完成，耗时 {:.1f} 秒",
                task_name,
                runtime.elapsed_seconds() or 0,
            )
        else:
            self._set_runtime_state(
                task_name,
                "failed",
                error=runtime.last_error or "任务执行失败",
            )
            mlog.warning("<<< [{}] 任务失败，本次不记录冷却时间", task_name)

        self._log_progress()
        self._check_shutdown()
        return True

    async def run_task(self, task_name: str, force: bool = False) -> bool:
        """执行一个任务；force 为真时忽略冷却时间。"""

        if task_name not in self.config.tasks:
            mlog.error("未知任务：{}", task_name)
            return False

        task_config = self.config.tasks[task_name]
        runtime = self._runtime[task_name]
        if not task_config.enabled:
            self._set_runtime_state(task_name, "disabled")
            return False
        if task_name in self._active_tasks:
            mlog.warning("[{}] 已经在运行，忽略重复触发", task_name)
            return False
        if not force and not self.should_run(task_name):
            self._set_runtime_state(task_name, "cooldown")
            self._session_done[task_name] = True
            mlog.debug("[{}] 仍在冷却中，跳过执行", task_name)
            return False

        self._active_tasks.add(task_name)
        self._session_done[task_name] = False
        runtime.started_at = datetime.now(tz=timezone.utc)
        runtime.finished_at = None
        runtime.last_error = None
        runtime.waiting_for_callback = False
        self._set_runtime_state(task_name, "starting")
        mlog.info(">>> [{}] 任务开始", task_name)

        try:
            definition = get_task_definition(task_name)
            if task_config.launcher.type == "application":
                await 启动并验证(task_config.launcher)
                runtime.waiting_for_callback = definition.completion_signal != "internal"
                self._set_runtime_state(task_name, "running")
                if definition.completion_signal == "internal":
                    self.mark_done(task_name)
                else:
                    mlog.info("[{}] 应用已启动，等待完成回调", task_name)
                return True

            if definition.runner is not None:
                result = await asyncio.to_thread(definition.runner)
                if isinstance(result, tuple) and result and result[0] is False:
                    raise RuntimeError("内置任务返回失败")
                if result is False:
                    raise RuntimeError("内置任务返回失败")
            self.mark_done(task_name, success=True)
            return True
        except Exception as exc:
            runtime.last_error = str(exc)
            mlog.exception("[{}] 任务执行异常", task_name)
            self.mark_done(task_name, success=False)
            return False
        finally:
            if runtime.state not in {"starting", "running"}:
                self._active_tasks.discard(task_name)

    async def timeout_watchdog(self) -> None:
        """定期检查自动化运行是否超过配置的超时时间。"""

        while not self._shutdown_triggered and not self._stop_requested:
            await asyncio.sleep(60)
            elapsed_hours = (
                datetime.now(tz=timezone.utc) - self._start_time
            ).total_seconds() / 3600
            if elapsed_hours < self.config.system.shutdown_timeout_hours:
                continue

            pending = [
                name for name, finished in self._session_done.items() if not finished
            ]
            for name in pending:
                runtime = self._runtime[name]
                runtime.finished_at = datetime.now(tz=timezone.utc)
                runtime.waiting_for_callback = False
                self._active_tasks.discard(name)
                self._set_runtime_state(name, "timed_out")
                self._session_done[name] = True
            report(f"运行 {elapsed_hours:.1f} 小时后超时，未完成任务：{pending}")
            if self.auto_shutdown:
                self._trigger_shutdown()
            return

    async def poll_loop(self) -> None:
        """在自动化模式下周期扫描到期任务。"""

        if not self.auto_schedule:
            mlog.info("桌面模式不启动自动任务扫描")
            return

        mlog.info("Scheduler 已启动，开始周期扫描任务")
        while not self._stop_requested:
            for task_name in list(self.config.tasks):
                if self.should_run(task_name):
                    await self.run_task(task_name)
            self._check_shutdown()
            if self._stop_requested:
                break
            await asyncio.sleep(self._poll_interval_seconds)

    def _load_state(self) -> None:
        """读取任务最后成功时间。"""

        if not self.config.db_path.exists():
            return
        try:
            data = json.loads(self.config.db_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._state = {
                    str(name): str(value)
                    for name, value in data.items()
                    if isinstance(value, str)
                }
        except (OSError, json.JSONDecodeError) as exc:
            mlog.warning("读取任务状态失败，将使用空状态：{}", exc)

    def _save_state(self) -> None:
        """保存任务最后成功时间。"""

        try:
            self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.db_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            mlog.error("保存任务状态失败：{}", exc)

    def _record_run(self, task_name: str) -> datetime:
        """记录任务成功完成时间。"""

        completed_at = datetime.now(tz=timezone.utc)
        self._state[task_name] = completed_at.isoformat()
        self._save_state()
        return completed_at

    def _apply_config(self, new_config: Config) -> None:
        """将新配置应用到当前运行时状态。"""

        old_runtime = self._runtime
        self.config = new_config
        self._runtime = {}
        new_session_done: dict[str, bool] = {}

        for name, task_config in self.config.tasks.items():
            previous = old_runtime.get(name)
            if previous is None:
                previous = TaskRuntime(name=name, state="pending")
            self._runtime[name] = previous

            if previous.state in {"starting", "running"}:
                new_session_done[name] = False
            elif not task_config.enabled:
                previous.waiting_for_callback = False
                previous.state = "disabled"
                new_session_done[name] = True
            elif previous.state in {"completed", "failed", "timed_out"}:
                new_session_done[name] = True
            else:
                should_run = self._should_run_raw(name)
                previous.state = "pending" if should_run else "cooldown"
                new_session_done[name] = not should_run

        self._session_done = new_session_done

    def _initialize_runtime_state(self) -> None:
        """根据配置和历史成功时间初始化本次会话状态。"""

        for name, task_config in self.config.tasks.items():
            last_success = self._parse_time(self._state.get(name))
            if not task_config.enabled:
                state: TaskState = "disabled"
                finished = True
            elif self._should_run_raw(name):
                state = "pending"
                finished = False
            else:
                state = "cooldown"
                finished = True
            self._runtime[name] = TaskRuntime(
                name=name,
                state=state,
                last_success_at=last_success,
            )
            if task_config.enabled:
                self._session_done[name] = finished

    def _parse_time(self, value: str | None) -> datetime | None:
        """解析历史时间字符串。"""

        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _should_run_raw(self, task_name: str) -> bool:
        """不检查 enabled 字段，只根据间隔时间判断。"""

        task_config = self.config.tasks.get(task_name)
        if not task_config:
            return False
        last_run = self._parse_time(self._state.get(task_name))
        if last_run is None:
            return True
        elapsed_hours = (
            datetime.now(tz=timezone.utc) - last_run
        ).total_seconds() / 3600
        return elapsed_hours >= task_config.interval_hours

    def _set_runtime_state(
        self,
        task_name: str,
        state: TaskState,
        error: str | None = None,
    ) -> None:
        """更新任务状态和最近错误。"""

        runtime = self._runtime[task_name]
        runtime.state = state
        if error is not None:
            runtime.last_error = error

    def _find_task_snapshot(self, task_name: str) -> dict[str, object]:
        """从状态快照中找到指定任务。"""

        for task in self.get_status_snapshot()["tasks"]:  # type: ignore[index]
            if task["name"] == task_name:
                return task  # type: ignore[return-value]
        raise KeyError(task_name)

    def _safe_system_config(self) -> dict[str, object]:
        """返回不泄露 SendKey 的全局配置。"""

        system = self.config.system.model_dump()
        configured = bool(system.pop("server_chan_key", ""))
        system["server_chan_key_configured"] = configured
        return system

    def _completion_description(self, signal: str) -> str:
        """返回页面使用的完成信号说明。"""

        return {
            "internal": "内置任务返回成功",
            "maa_post": "等待 MAA POST /maa 回调",
            "maa_get": "等待 MaaEnd GET /maa 回调",
        }.get(signal, "等待任务完成")

    def _log_initial_status(self) -> None:
        """记录启动时的任务状态。"""

        pending = [
            name for name, runtime in self._runtime.items() if runtime.state == "pending"
        ]
        cooldown = [
            name for name, runtime in self._runtime.items() if runtime.state == "cooldown"
        ]
        mlog.info("本次会话待完成任务：{}", pending)
        if cooldown:
            mlog.info("冷却中任务：{}", cooldown)

    def _log_progress(self) -> None:
        """记录当前会话进度。"""

        finished = [name for name, value in self._session_done.items() if value]
        pending = [name for name, value in self._session_done.items() if not value]
        mlog.info(
            "任务进度 {}/{}，已完成：{}，等待中：{}",
            len(finished),
            len(self._session_done),
            finished,
            pending,
        )

    def _check_shutdown(self) -> None:
        """在自动化模式下检查是否可以执行完成动作。"""

        if not self.auto_shutdown or not self.auto_schedule or self._shutdown_triggered:
            return
        if not self._session_done or all(self._session_done.values()):
            self._trigger_shutdown()

    def _trigger_shutdown(self) -> None:
        """执行配置中的自动化完成动作。"""

        self._shutdown_triggered = True
        elapsed = (datetime.now(tz=timezone.utc) - self._start_time).total_seconds()
        hours, remainder = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(remainder, 60)
        report(f"本次自动化任务完成，总用时 {hours} 小时 {minutes} 分 {seconds} 秒")
        if self.config.system.server_chan_key:
            push_wechat(self.config.system.server_chan_key)

        if not self.config.system.shutdown_on_complete:
            mlog.info("已关闭自动完成后的系统电源操作")
            self._request_stop()
            return

        action = self.config.system.completion_action
        delay = self.config.system.shutdown_delay_seconds
        if action == "none":
            mlog.info("任务完成，不执行系统电源操作")
            self._request_stop()
            return

        mlog.info("系统将在 {} 秒后执行 {}", delay, action)
        if platform.system() == "Windows" and action == "shutdown":
            os.system(f"shutdown /s /t {delay}")
        elif platform.system() == "Windows" and action == "hibernate":
            time.sleep(delay)
            os.system("shutdown /h")
        elif platform.system() == "Windows" and action == "sleep":
            os.system(
                "cmd /c start \"\" powershell.exe -NoProfile -ExecutionPolicy Bypass "
                f"-Command \"Start-Sleep -Seconds {delay}; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.Application]::SetSuspendState("
                "[System.Windows.Forms.PowerState]::Suspend, $false, $false)\""
            )
        else:
            mlog.warning("当前系统不支持配置的电源操作，仅退出自动化进程")
        self._request_stop()

    def _request_stop(self) -> None:
        """请求主程序停止。"""

        self._stop_requested = True
        if self._stop_event is not None:
            self._stop_event.set()
