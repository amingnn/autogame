"""扫描到期任务并完成一次无界面自动化会话。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from autogame.config import Config
from autogame.logger import mlog
from autogame.notify import clear_report, push_wechat, report_sections
from autogame.runtime.execution_lock import ExecutionLock
from autogame.runtime.power import PowerController
from autogame.task_manager import TaskManager


class AutomationRunner:
    """负责一次计划任务触发后的自动化策略。"""

    def __init__(self, config: Config, force: bool = False) -> None:
        self.config = config
        self.force = force
        self.manager = TaskManager(config)
        self._instance_lock = ExecutionLock(config.paths.lock_dir)
        self._power = PowerController()

    async def run(self) -> bool:
        """运行全部到期任务并执行会话完成策略。"""

        lease = self._instance_lock.acquire("automation-instance")
        if lease is None:
            mlog.warning("另一个 AutoGame 自动化实例正在运行，本次触发已跳过")
            return False

        clear_report()
        started_at = datetime.now(tz=timezone.utc)
        stop_event = asyncio.Event()
        monitor_task = asyncio.create_task(self.manager.monitor_loop(stop_event))
        enabled_targets = [
            name for name, task in self.config.tasks.items() if task.enabled
        ]
        targets = (
            enabled_targets
            if self.force
            else [name for name in enabled_targets if self.manager.should_run(name)]
        )
        cooldown = [
            name for name in enabled_targets if name not in targets
        ]
        mlog.info("冷却中任务：{}", self._format_task_names(cooldown))
        mlog.info("待执行任务：{}", self._format_task_names(targets))
        timed_out = False
        try:
            await asyncio.gather(
                *(
                    self.manager.run_task(task_name, force=self.force)
                    for task_name in targets
                )
            )

            timeout_seconds = self.config.system.automation_timeout_minutes * 60
            startup_elapsed = (
                datetime.now(tz=timezone.utc) - started_at
            ).total_seconds()
            remaining_seconds = max(0.0, timeout_seconds - startup_elapsed)
            completed = await self.manager.wait_for_tasks(
                set(targets),
                remaining_seconds,
            )
            if not completed:
                timed_out = True
                await self.manager.timeout_tasks(set(targets))
        finally:
            stop_event.set()
            await monitor_task
            await self.manager.shutdown()
            lease.release()

        elapsed = int((datetime.now(tz=timezone.utc) - started_at).total_seconds())
        results = [self.manager.get_task_result(name) for name in targets]
        all_succeeded = bool(targets) and all(
            result["state"] == "completed" for result in results
        )
        notification_sections = self._build_report_sections(
            results,
            elapsed,
            timed_out=timed_out,
            completion_action_enabled=(
                bool(targets) and self.config.system.completion_action != "none"
            ),
        )
        report_sections(notification_sections)

        should_send_server_chan = (
            bool(targets)
            and set(targets) == set(enabled_targets)
            and self.config.system.server_chan_enabled
            and bool(self.config.system.server_chan_key)
        )
        if should_send_server_chan:
            push_wechat(self.config.system.server_chan_key)
        elif (
            targets
            and self.config.system.server_chan_enabled
            and self.config.system.server_chan_key
        ):
            mlog.info("本次不是全部运行，跳过 Server 酱通知")
        if targets and self.config.system.completion_action != "none":
            if not all_succeeded:
                mlog.warning("存在失败或超时任务，仍执行强制系统完成动作")
            await self._power.execute(
                self.config.system.completion_action,
                self.config.system.completion_action_delay_seconds,
            )
        elif not targets:
            mlog.info("没有到期任务，不执行系统完成动作")
        else:
            mlog.info("完成动作配置为 none，不执行系统操作")
        return not targets or all_succeeded

    @staticmethod
    def _format_task_names(task_names: list[str]) -> str:
        """把任务名称格式化为紧凑的中文列表。"""

        return "、".join(task_names) if task_names else "无"

    def _build_report_sections(
        self,
        results: list[dict[str, object]],
        elapsed_seconds: int,
        timed_out: bool,
        completion_action_enabled: bool,
    ) -> list[str]:
        """生成需要分别包装为 Markdown 代码块的通知段落。"""

        state_names = {
            "completed": "完成",
            "failed": "失败",
            "timed_out": "超时",
        }
        results_by_name: dict[str, dict[str, object]] = {}
        detail_sections: list[str] = []
        for result in results:
            name = str(result["name"])
            state = state_names.get(str(result["state"]), str(result["state"]))
            duration = float(result["elapsed_seconds"])
            lines = [str(line) for line in result["lines"]]
            results_by_name[name] = result
            body = [
                f"{name}：{state} ({self._format_duration(duration)})",
                *lines,
            ]
            if result["error"]:
                body.append(f"错误：{result['error']}")
            detail_sections.append("\n".join(body))

        task_names = list(self.config.tasks)
        task_names.extend(
            name for name in results_by_name if name not in self.config.tasks
        )
        summary_section = ["任务总结", "任务列表："]
        for name in task_names:
            result = results_by_name.get(name)
            if result is not None:
                state = state_names.get(
                    str(result["state"]),
                    str(result["state"]),
                )
                duration = self._format_duration(float(result["elapsed_seconds"]))
                summary_section.append(f"- {name}：{state}（用时 {duration}）")
            elif self.config.tasks[name].enabled:
                summary_section.append(f"- {name}：冷却中")
            else:
                summary_section.append(f"- {name}：已关闭")
        if not task_names:
            summary_section.append("- 无任务")

        if timed_out:
            summary_section.append("自动化会话：达到全局超时")
        summary_section.append(f"总用时：{self._format_duration(elapsed_seconds)}")

        if completion_action_enabled:
            summary_section.append(
                "完成后动作："
                f"{self.config.system.completion_action}，延迟 "
                f"{self.config.system.completion_action_delay_seconds} 秒"
            )
        elif not results:
            summary_section.append("完成后动作：未执行（没有到期任务）")
        else:
            summary_section.append("完成后动作：未执行（配置为 none）")

        sections = [
            "\n".join(summary_section),
            *detail_sections,
        ]
        return sections

    @staticmethod
    def _format_duration(seconds: float | int) -> str:
        """把秒数转换为便于阅读的分钟和秒。"""

        rounded_seconds = max(0, int(round(seconds)))
        minutes, remaining_seconds = divmod(rounded_seconds, 60)
        return f"{minutes} 分 {remaining_seconds} 秒"


async def run_automation(config: Config, force: bool = False) -> bool:
    """创建并运行一次自动化会话。"""

    return await AutomationRunner(config, force=force).run()
