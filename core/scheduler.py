import asyncio
import importlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from core.common import Config
from core.logger import mlog

POLL_INTERVAL = 300  # 每 5 分钟检查一次


class Scheduler:
    def __init__(self, config: Config) -> None:
        self.config = config
        # 冷却记录：{task_name: ISO 8601 str}，持久化到 db_path
        self._state: dict[str, str] = {}
        # 本次会话完成标记：{task_name: bool}，内存级，重启清零
        self._session_done: dict[str, bool] = {
            name: False
            for name, cfg in config.tasks.items()
            if cfg.enabled
        }
        self._shutdown_triggered = False
        self._start_time = datetime.now(tz=timezone.utc)
        self._load_state()

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

    def should_run(self, task_name: str) -> bool:
        task_cfg = self.config.tasks.get(task_name)
        if not task_cfg or not task_cfg.enabled:
            return False

        last_str = self._state.get(task_name)
        if not last_str:
            return True

        try:
            last_run = datetime.fromisoformat(last_str)
        except ValueError:
            return True

        now = datetime.now(tz=timezone.utc)
        elapsed_hours = (now - last_run).total_seconds() / 3600
        return elapsed_hours >= task_cfg.interval_hours

    def _record_run(self, task_name: str) -> None:
        self._state[task_name] = datetime.now(tz=timezone.utc).isoformat()
        self._save_state()

    # ── 会话完成状态 ─────────────────────────────────────────────────────────

    def mark_done(self, task_name: str) -> None:
        """由 webhook 回调调用，标记任务本次会话已完成。"""
        if task_name not in self._session_done:
            mlog.warning(f"[{task_name}] mark_done 被调用，但该任务不在会话中")
            return
        if not self._session_done[task_name]:
            self._session_done[task_name] = True
            mlog.info(f"[{task_name}] 已标记完成")
        self._check_shutdown()

    def _log_progress(self) -> None:
        done = [k for k, v in self._session_done.items() if v]
        total = len(self._session_done)
        mlog.info(f"任务进度: {len(done)}/{total} 已完成 {done}")

    def _check_shutdown(self) -> None:
        """检查所有启用任务是否都已完成，若是则触发关机。"""
        if self._shutdown_triggered:
            return
        if not self.config.system.shutdown_on_complete:
            return
        self._log_progress()
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
            mlog.debug(f"[{task_name}] 未达到触发间隔，跳过")
            return False

        module_path = f"tasks.{task_name}.client"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            mlog.error(f"[{task_name}] 找不到模块: {module_path}")
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
            mlog.info(f"[{task_name}] run() 结束，耗时 {elapsed:.1f}s")
            self._record_run(task_name)

            # 非 webhook_notify 任务：run() 结束即视为完成
            task_cfg = self.config.tasks[task_name]
            if not task_cfg.webhook_notify:
                self.mark_done(task_name)

            return True
        except Exception as e:
            mlog.error(f"[{task_name}] 执行失败: {e}", exc_info=True)
            return False

    # ── 轮询循环 ────────────────────────────────────────────────────────────

    async def poll_loop(self) -> None:
        mlog.info("Scheduler 轮询已启动")
        while True:
            # 超时保护
            if not self._shutdown_triggered:
                elapsed_hours = (
                    datetime.now(tz=timezone.utc) - self._start_time
                ).total_seconds() / 3600
                if elapsed_hours >= self.config.system.shutdown_timeout_hours:
                    done = [k for k, v in self._session_done.items() if v]
                    mlog.error(
                        f"超时保护触发（运行超过 {self.config.system.shutdown_timeout_hours}h），"
                        f"已完成任务: {done}"
                    )
                    self._trigger_shutdown()

            for task_name in self.config.tasks:
                await self.run_task(task_name)

            await asyncio.sleep(POLL_INTERVAL)
