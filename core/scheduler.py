import asyncio
import importlib
import inspect
import json
import os
import platform
import sys
from datetime import datetime, timezone
from typing import Callable

from core.common import Config
from core.logger import mlog
from core.notify import report


class Scheduler:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._state: dict[str, str] = {}
        self._load_state()

        self._session_done: dict[str, bool] = {
            name: not self._should_run_raw(name)
            for name, cfg in config.tasks.items()
            if cfg.enabled
        }
        self._shutdown_triggered = False
        self._start_time = datetime.now(tz=timezone.utc)
        # 每个任务的开始时间，用于 webhook 回调时计算耗时
        self._task_start_times: dict[str, datetime] = {}
        self._log_initial_status()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        path = self.config.db_path
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
            except Exception as e:
                mlog.warning(f"加载任务状态失败，将使用空状态: {e}")
                self._state = {}

    def _save_state(self) -> None:
        path = self.config.db_path
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            mlog.error(f"保存任务状态失败: {e}")

    # ── 冷却校验 ────────────────────────────────────────────────────────────

    def _should_run_raw(self, task_name: str) -> bool:
        task_cfg = self.config.tasks.get(task_name)
        if not task_cfg:
            return False
        last_str = self._state.get(task_name)
        if not last_str:
            return True
        try:
            last_run = datetime.fromisoformat(last_str)
        except ValueError:
            return True
        elapsed_hours = (
            datetime.now(tz=timezone.utc) - last_run
        ).total_seconds() / 3600
        return elapsed_hours >= task_cfg.interval_hours

    def should_run(self, task_name: str) -> bool:
        task_cfg = self.config.tasks.get(task_name)
        if not task_cfg or not task_cfg.enabled:
            return False
        return self._should_run_raw(task_name)

    def _record_run(self, task_name: str) -> None:
        self._state[task_name] = datetime.now(tz=timezone.utc).isoformat()
        self._save_state()

    # ── 会话状态 ─────────────────────────────────────────────────────────────

    def _log_initial_status(self) -> None:
        pending = [k for k, v in self._session_done.items() if not v]
        skipped = [k for k, v in self._session_done.items() if v]
        mlog.info(f"本次会话待完成任务: {pending}")
        if skipped:
            mlog.info(f"冷却中（跳过）: {skipped}")

    def mark_done(self, task_name: str, success: bool = True) -> None:
        """由 webhook 回调或 entry 函数返回时调用。
        success=False 时仅解除阻塞，不记录冷却时间（下次仍重试）。
        """
        if task_name not in self._session_done:
            mlog.warning(f"[{task_name}] mark_done 被调用，但该任务不在本次会话中")
            return
        if not self._session_done[task_name]:
            self._session_done[task_name] = True
            if success:
                self._record_run(task_name)
                start = self._task_start_times.get(task_name)
                if start:
                    elapsed = (datetime.now() - start).total_seconds()
                    mlog.info(f"<<< [{task_name}] 任务完成，耗时 {elapsed:.1f}s")
                else:
                    mlog.info(f"<<< [{task_name}] 任务完成")
            else:
                mlog.warning(f"<<< [{task_name}] 任务失败，已解除阻塞（不记录冷却）")
        self._log_progress()
        self._check_shutdown()

    def _log_progress(self) -> None:
        done = [k for k, v in self._session_done.items() if v]
        pending = [k for k, v in self._session_done.items() if not v]
        total = len(self._session_done)
        mlog.info(
            f"进度 {len(done)}/{total} | 已完成: {done} | 等待中: {pending}"
        )

    def _check_shutdown(self) -> None:
        if self._shutdown_triggered:
            return
        if not self.config.system.shutdown_on_complete:
            return
        if all(self._session_done.values()):
            mlog.info("所有任务均已完成！")
            self._trigger_shutdown()

    def _trigger_shutdown(self) -> None:
        self._shutdown_triggered = True
        elapsed = (datetime.now(tz=timezone.utc) - self._start_time).total_seconds()
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        report(f"所有任务完成，总用时 {h}h {m}m {s}s")
        self._push_report()
        delay = self.config.system.shutdown_delay_seconds
        mlog.info(f"系统将在 {delay} 秒后关机...")
        if platform.system() == "Windows":
            os.system(f"shutdown /s /t {delay}")
        else:
            mlog.warning("非 Windows 系统，跳过关机命令（仅退出进程）")
            sys.exit(0)

    def _push_report(self) -> None:
        key = self.config.system.server_chan_key
        if not key:
            return
        from core.notify import push_wechat
        push_wechat(key)

    # ── 入口函数解析 ──────────────────────────────────────────────────────────

    def _resolve_entry(self, task_name: str) -> Callable | None:
        """解析 task_cfg.entry 为可调用对象。entry 为空时返回 None。"""
        task_cfg = self.config.tasks[task_name]
        if not task_cfg.entry:
            return None

        parts = task_cfg.entry.rsplit(".", 1)
        if len(parts) != 2:
            mlog.error(f"[{task_name}] entry 格式无效（需为 module.function）: {task_cfg.entry}")
            return None

        module_dotted, func_name = parts
        module_path = f"tasks.{module_dotted}"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            mlog.error(f"[{task_name}] 加载模块失败 {module_path}: {e}")
            return None

        fn = getattr(module, func_name, None)
        if fn is None:
            mlog.error(f"[{task_name}] 函数未找到: {func_name} in {module_path}")
        return fn

    # ── 任务执行 ────────────────────────────────────────────────────────────

    async def run_task(self, task_name: str, force: bool = False) -> bool:
        """按配置执行任务。force=True 时跳过冷却校验。"""
        if not force and not self.should_run(task_name):
            mlog.debug(f"[{task_name}] 冷却中，跳过")
            return False

        task_cfg = self.config.tasks[task_name]
        run_fn = self._resolve_entry(task_name)

        if run_fn is None and task_cfg.entry:
            # entry 指定了但加载失败
            mlog.error(f"[{task_name}] entry 解析失败，任务中止")
            self.mark_done(task_name, success=False)
            return False

        # 记录任务开始时间
        start = datetime.now()
        self._task_start_times[task_name] = start
        mlog.info(f">>> [{task_name}] 任务开始")

        if run_fn is None:
            # 无 entry 函数（如 maa），等待 webhook 回调
            mlog.info(f"    [{task_name}] 无入口函数，等待 webhook 回调完成")
            return True

        try:
            if inspect.iscoroutinefunction(run_fn):
                await run_fn()
            else:
                await asyncio.to_thread(run_fn)

            if task_cfg.done_on == "entry":
                # entry 返回即完成，mark_done 内部会输出结束日志
                self.mark_done(task_name, success=True)
            else:
                elapsed = (datetime.now() - start).total_seconds()
                mlog.info(f"    [{task_name}] entry 执行完毕 ({elapsed:.1f}s)，等待 webhook 回调")
            return True
        except Exception as e:
            elapsed = (datetime.now() - start).total_seconds()
            mlog.error(f"<<< [{task_name}] 任务异常（{elapsed:.1f}s）: {e}", exc_info=True)
            self.mark_done(task_name, success=False)
            return False

    # ── 轮询循环 ────────────────────────────────────────────────────────────

    async def timeout_watchdog(self) -> None:
        """独立超时监控协程，每 60 秒检查一次，不依赖轮询周期。
        解决 poll_interval > shutdown_timeout 时超时检查无法及时触发的问题。
        """
        while not self._shutdown_triggered:
            await asyncio.sleep(60)
            if self._shutdown_triggered:
                break
            elapsed_hours = (
                datetime.now(tz=timezone.utc) - self._start_time
            ).total_seconds() / 3600
            if elapsed_hours >= self.config.system.shutdown_timeout_hours:
                pending = [k for k, v in self._session_done.items() if not v]
                report(f"已运行 {elapsed_hours:.1f}h，监控超时，准备关机，未完成: {pending}")
                self._trigger_shutdown()
                break

    async def poll_loop(self) -> None:
        mlog.info("Scheduler 轮询已启动，等待任务触发或 Webhook 回调...")
        for task_name in self.config.tasks:
            await self.run_task(task_name)
        mlog.info("初始任务扫描完成，等待 webhook 回调或超时...")
