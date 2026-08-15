"""测试共享任务管理器和自动化策略。"""

from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from autogame.automation.runner import AutomationRunner
from autogame.config import AppPaths, Config, SystemConfig, TaskConfig
from autogame.registry import TaskDefinition
from autogame.task_manager import TaskManager
from autogame.tasks.base import AdapterResult, StartResult, TaskContext


class ImmediateTask:
    """测试用立即完成任务。"""

    description = "立即完成"
    requires_script = False

    async def start(self, context: TaskContext) -> StartResult:
        return StartResult(None, AdapterResult("completed"))

    async def poll(self, context: TaskContext, handle: object) -> AdapterResult:
        return AdapterResult("completed")

    async def stop(self, context: TaskContext, handle: object) -> None:
        return None


class PolledTask:
    """测试用轮询后完成任务。"""

    description = "轮询完成"
    requires_script = False

    async def start(self, context: TaskContext) -> StartResult:
        return StartResult(object(), AdapterResult("running", waiting_for_completion=True))

    async def poll(self, context: TaskContext, handle: object) -> AdapterResult:
        return AdapterResult("completed")

    async def stop(self, context: TaskContext, handle: object) -> None:
        return None


class FailedTask(ImmediateTask):
    """测试用立即失败任务。"""

    async def start(self, context: TaskContext) -> StartResult:
        return StartResult(None, AdapterResult("failed", "测试失败"))


class ReportingTask(ImmediateTask):
    """测试用带业务摘要的成功任务。"""

    async def start(self, context: TaskContext) -> StartResult:
        return StartResult(
            None,
            AdapterResult("completed", report_lines=("完成任务: 测试任务",)),
        )


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    """验证统一任务状态流转。"""

    def make_config(self, root: Path, enabled: bool = True) -> Config:
        return Config(
            paths=AppPaths(root=root),
            system=SystemConfig(completion_action="none"),
            tasks={"demo": TaskConfig(enabled=enabled)},
        )

    async def test_immediate_task_completes_and_saves_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            manager = TaskManager(config)
            definition = TaskDefinition(
                "demo",
                lambda _config: ImmediateTask(),
                "立即完成",
                False,
            )
            with patch(
                "autogame.task_manager.get_task_definition",
                return_value=definition,
            ):
                self.assertTrue(await manager.run_task("demo", force=True))
            self.assertEqual(manager.get_task_state("demo"), "completed")
            self.assertTrue(config.db_path.exists())

    async def test_polled_task_stays_running_until_monitor_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(self.make_config(Path(directory)))
            definition = TaskDefinition(
                "demo",
                lambda _config: PolledTask(),
                "轮询完成",
                False,
            )
            with patch(
                "autogame.task_manager.get_task_definition",
                return_value=definition,
            ):
                self.assertTrue(await manager.run_task("demo", force=True))
                self.assertEqual(manager.get_task_state("demo"), "running")
                await manager.poll_active_tasks()
            self.assertEqual(manager.get_task_state("demo"), "completed")

    async def test_automation_with_no_due_tasks_exits_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory), enabled=False)
            runner = AutomationRunner(config)
            self.assertTrue(await runner.run())

    async def test_automation_starts_due_tasks_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            started: set[str] = set()
            all_started = asyncio.Event()

            class ConcurrentTask(ImmediateTask):
                async def start(self, context: TaskContext) -> StartResult:
                    started.add(context.task_name)
                    if len(started) == 2:
                        all_started.set()
                    await asyncio.wait_for(all_started.wait(), timeout=0.2)
                    return StartResult(None, AdapterResult("completed"))

            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(completion_action="none"),
                tasks={
                    "first": TaskConfig(enabled=True),
                    "second": TaskConfig(enabled=True),
                },
            )
            runner = AutomationRunner(config)

            def definition(name: str) -> TaskDefinition:
                return TaskDefinition(name, lambda _config: ConcurrentTask(), name, False)

            with patch(
                "autogame.task_manager.get_task_definition",
                side_effect=definition,
            ):
                self.assertTrue(await runner.run())
            self.assertEqual(started, {"first", "second"})

    async def test_failed_task_still_executes_completion_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(completion_action="hibernate"),
                tasks={
                    "success": TaskConfig(enabled=True),
                    "failure": TaskConfig(enabled=True),
                },
            )
            runner = AutomationRunner(config)
            runner._power.execute = AsyncMock()  # type: ignore[method-assign]

            def definition(name: str) -> TaskDefinition:
                task_type = FailedTask if name == "failure" else ImmediateTask
                return TaskDefinition(name, lambda _config: task_type(), name, False)

            with patch(
                "autogame.task_manager.get_task_definition",
                side_effect=definition,
            ):
                self.assertFalse(await runner.run())
            runner._power.execute.assert_awaited_once_with("hibernate", 60)  # type: ignore[attr-defined]

    async def test_timed_out_task_still_executes_completion_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(completion_action="hibernate"),
                tasks={"long_running": TaskConfig(enabled=True)},
            )
            runner = AutomationRunner(config)
            runner._power.execute = AsyncMock()  # type: ignore[method-assign]

            class NeverEndingTask(PolledTask):
                async def poll(
                    self,
                    context: TaskContext,
                    handle: object,
                ) -> AdapterResult:
                    return AdapterResult("running", waiting_for_completion=True)

            runner.manager.wait_for_tasks = AsyncMock(return_value=False)  # type: ignore[method-assign]
            definition = TaskDefinition(
                "long_running",
                lambda _config: NeverEndingTask(),
                "持续运行",
                False,
            )

            with patch(
                "autogame.task_manager.get_task_definition",
                return_value=definition,
            ):
                self.assertFalse(await runner.run())

            runner._power.execute.assert_awaited_once_with("hibernate", 60)  # type: ignore[attr-defined]

    async def test_disabled_server_chan_skips_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(
                    completion_action="none",
                    server_chan_enabled=False,
                    server_chan_key="secret-value",
                ),
                tasks={"demo": TaskConfig(enabled=True)},
            )
            runner = AutomationRunner(config)
            definition = TaskDefinition(
                "demo",
                lambda _config: ImmediateTask(),
                "立即完成",
                False,
            )

            with (
                patch("autogame.task_manager.get_task_definition", return_value=definition),
                patch("autogame.automation.runner.push_wechat") as push_wechat,
                patch("autogame.automation.runner.report_sections"),
            ):
                self.assertTrue(await runner.run())

            push_wechat.assert_not_called()

    async def test_successful_tasks_execute_action_and_report_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(
                    completion_action="hibernate",
                    completion_action_delay_seconds=15,
                ),
                tasks={"reporting": TaskConfig(enabled=True)},
            )
            runner = AutomationRunner(config)
            runner._power.execute = AsyncMock()  # type: ignore[method-assign]
            definition = TaskDefinition(
                "reporting",
                lambda _config: ReportingTask(),
                "报告任务",
                False,
            )

            with (
                patch(
                    "autogame.task_manager.get_task_definition",
                    return_value=definition,
                ),
                patch("autogame.automation.runner.report_sections") as write_report,
            ):
                self.assertTrue(await runner.run())

            runner._power.execute.assert_awaited_once_with("hibernate", 15)  # type: ignore[attr-defined]
            report_sections = write_report.call_args.args[0]
            self.assertTrue(
                any("完成任务: 测试任务" in section for section in report_sections)
            )
