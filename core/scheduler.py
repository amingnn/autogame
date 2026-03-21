import asyncio
import importlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from core.common import Config
from core.logger import mlog, report


class Scheduler:
    def __init__(self, config: Config) -> None:
        self.config = config
        # 冷却记录：{task_name: ISO 8601 str}，持久化
        self._state: dict[str, str] = {}
        self._load_state()

        # 本次会话完成标记：
        #   需要运行 → False（等待完成）
        #   不需运行（冷却未到）→ True（视为已完成，不阻塞关机）
        self._session_done: dict[str, bool] = {
            name: not self._should_run_raw(name)
            for name, cfg in config.tasks.items()
            if cfg.enabled
        }
        self._shutdown_triggered = False
        self._start_time = datetime.now(tz=timezone.utc)
        self._log_initial_status()

    # ── 持久化（冷却时间） ────────────────────────────────────────────────────

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
        """仅检查冷却，不校验 enabled。供初始化时调用。"""
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

    # ── 会话完成状态 ─────────────────────────────────────────────────────────

    def _log_initial_status(self) -> None:
        pending = [k for k, v in self._session_done.items() if not v]
        skipped = [k for k, v in self._session_done.items() if v]
        mlog.info(f"本次会话待完成任务: {pending}")
        if skipped:
            mlog.info(f"冷却中（跳过）: {skipped}")

    def mark_done(self, task_name: str) -> None:
        """由 webhook 回调或 run() 自报完成时调用。"""
        if task_name not in self._session_done:
            mlog.warning(f"[{task_name}] mark_done 被调用，但该任务不在本次会话中")
            return
        if not self._session_done[task_name]:
            self._session_done[task_name] = True
            mlog.info(f"[{task_name}] ✓ 已完成")
        self._log_progress()
        self._check_shutdown()

    def _log_progress(self) -> None:
        done = [k for k, v in self._session_done.items() if v]
        pending = [k for k, v in self._session_done.items() if not v]
        total = len(self._session_done)
        mlog.info(
            f"进度 {len(done)}/{total} | "
            f"已完成: {done} | "
            f"等待中: {pending}"
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
        from core.logger import push_wechat
        push_wechat(key)

    # ── 任务执行 ────────────────────────────────────────────────────────────

    async def run_task(self, task_name: str, force: bool = False) -> bool:
        """动态加载 tasks/<task_name>/client.py 并调用 run()。
        force=True 时跳过冷却校验。
        """
        if not force and not self.should_run(task_name):
            mlog.debug(f"[{task_name}] 冷却中，跳过")
            return False

        module_path = f"tasks.{task_name}.client"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            mlog.error(f"[{task_name}] 找不到模块 {module_path}: {e}")
            return False

        run_fn = getattr(module, "run", None)
        if not run_fn:
            mlog.error(f"[{task_name}] client.py 未暴露 run() 函数")
            return False

        mlog.info(f"[{task_name}] 开始执行")
        start = datetime.now()
        try:
            if asyncio.iscoroutinefunction(run_fn):
                await run_fn()
            else:
                await asyncio.to_thread(run_fn)
            elapsed = (datetime.now() - start).total_seconds()
            mlog.info(f"[{task_name}] run() 返回，耗时 {elapsed:.1f}s")
            self._record_run(task_name)
            # webhook_notify=True 的任务由 POST /done 或 /maa 等回调标记完成
            # webhook_notify=False 的任务在这里直接标记完成
            task_cfg = self.config.tasks[task_name]
            if not task_cfg.webhook_notify:
                self.mark_done(task_name)
            return True
        except Exception as e:
            mlog.error(f"[{task_name}] 执行失败: {e}", exc_info=True)
            # 失败也标记完成，避免永久阻塞关机
            self.mark_done(task_name)
            return False

    # ── 轮询循环 ────────────────────────────────────────────────────────────

    async def poll_loop(self) -> None:
        mlog.info("Scheduler 轮询已启动，等待任务触发或 Webhook 回调...")
        while True:
            # 超时保护
            if not self._shutdown_triggered:
                elapsed_hours = (
                    datetime.now(tz=timezone.utc) - self._start_time
                ).total_seconds() / 3600
                if elapsed_hours >= self.config.system.shutdown_timeout_hours:
                    pending = [k for k, v in self._session_done.items() if not v]
                    report(
                        f"监控超时，为安全起见准备关机，未完成: {pending}"
                    )
                    self._trigger_shutdown()

            for task_name in self.config.tasks:
                await self.run_task(task_name)

            interval_seconds = self.config.system.poll_interval_hours * 3600
            await asyncio.sleep(interval_seconds)
