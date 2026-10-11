"""测试进程之外的状态、锁和日志读取能力。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import psutil

from autogame.config import TaskConfig
from autogame.runtime.execution_lock import ExecutionLock
from autogame.runtime.log_reader import IncrementalLogReader
from autogame.runtime.process import (
    ProcessHandle,
    _terminate_process_tree,
    find_processes,
    process_is_running,
    start_process,
)
from autogame.runtime.state_store import StateStore
from autogame.tasks.core.base import TaskContext
from autogame.tasks.maa import MaaAdapter
from autogame.tasks.maaend import MaaEndAdapter
from autogame.tasks.core.process_script import ProcessRun


class RuntimeTests(unittest.TestCase):
    """验证跨任务共享的运行能力。"""

    def test_log_reader_only_returns_new_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "debug.log"
            path.write_text("旧日志\n", encoding="utf-8")
            reader = IncrementalLogReader([path])
            reader.prime()
            with path.open("a", encoding="utf-8") as stream:
                stream.write("新日志\n")
            self.assertEqual(reader.read_lines(), [(path, "新日志")])

    def test_state_store_merges_success_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            now = datetime.now(tz=timezone.utc)
            store.record_success("maa", now)
            store.record_success("maaend", now)
            self.assertEqual(set(store.load()), {"maa", "maaend"})

    def test_execution_lock_prevents_duplicate_holder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            locks = ExecutionLock(Path(directory))
            first = locks.acquire("task-maa")
            self.assertIsNotNone(first)
            self.assertIsNone(locks.acquire("task-maa"))
            first.release()  # type: ignore[union-attr]
            second = locks.acquire("task-maa")
            self.assertIsNotNone(second)
            second.release()  # type: ignore[union-attr]

    def test_process_lookup_requires_exact_executable_path(self) -> None:
        expected = Path("D:/game/MaaEnd/MaaEnd.exe")
        matching = SimpleNamespace(
            info={"name": "MaaEnd.exe", "exe": str(expected)}
        )
        different = SimpleNamespace(
            info={"name": "MaaEnd.exe", "exe": "D:/other/MaaEnd.exe"}
        )

        with patch(
            "autogame.runtime.process.psutil.process_iter",
            return_value=[matching, different],
        ):
            result = find_processes("MaaEnd.exe", expected)

        self.assertEqual(result, [matching])

    def test_process_lookup_ignores_process_that_exits_during_lookup(self) -> None:
        class VanishedProcess:
            @property
            def info(self) -> dict[str, str]:
                raise psutil.NoSuchProcess(39856)

        matching = SimpleNamespace(
            info={
                "name": "MaaEnd.exe",
                "exe": "D:/game/MaaEnd/MaaEnd.exe",
            }
        )

        with patch(
            "autogame.runtime.process.psutil.process_iter",
            return_value=[VanishedProcess(), matching],
        ):
            result = find_processes(
                "MaaEnd.exe",
                Path("D:/game/MaaEnd/MaaEnd.exe"),
            )

        self.assertEqual(result, [matching])

    def test_owned_process_handle_follows_same_path_after_pid_replacement(self) -> None:
        replacement = Mock()
        handle = ProcessHandle(
            10,
            "MaaEnd.exe",
            100.0,
            True,
            False,
            Path("D:/game/MaaEnd/MaaEnd.exe"),
        )

        with (
            patch(
                "autogame.runtime.process.psutil.Process",
                side_effect=psutil.NoSuchProcess(10),
            ),
            patch(
                "autogame.runtime.process.find_processes",
                return_value=[replacement],
            ) as find_processes_mock,
        ):
            self.assertTrue(process_is_running(handle))

        find_processes_mock.assert_called_once_with(
            "MaaEnd.exe",
            Path("D:/game/MaaEnd/MaaEnd.exe"),
        )

    def test_start_process_restarts_matching_old_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "MaaEnd.exe"
            executable.write_bytes(b"test")
            old_process = Mock(pid=10)
            new_process = Mock(pid=20)
            new_process.create_time.return_value = 100.0

            with (
                patch(
                    "autogame.runtime.process.find_processes",
                    side_effect=[[old_process], [], [new_process]],
                ),
                patch("autogame.runtime.process._terminate_process_tree") as terminate,
                patch("autogame.runtime.process.subprocess.Popen"),
                patch("autogame.runtime.process.time.time", return_value=100.0),
                patch(
                    "autogame.runtime.process.time.monotonic",
                    side_effect=[0.0, 0.0, 0.0],
                ),
            ):
                handle = start_process(
                    executable,
                    "MaaEnd.exe",
                    restart_existing=True,
                    allow_existing=False,
                )

        terminate.assert_called_once_with(old_process)
        self.assertEqual(handle.pid, 20)
        self.assertTrue(handle.restarted)

    def test_start_process_rechecks_old_instance_after_termination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "MaaEnd.exe"
            executable.write_bytes(b"test")
            old_process = Mock(pid=10)
            new_process = Mock(pid=20)
            new_process.create_time.return_value = 100.0

            with (
                patch(
                    "autogame.runtime.process.find_processes",
                    side_effect=[
                        [old_process],
                        [old_process],
                        [],
                        [new_process],
                    ],
                ),
                patch("autogame.runtime.process._terminate_process_tree"),
                patch("autogame.runtime.process.subprocess.Popen"),
                patch("autogame.runtime.process.time.time", return_value=100.0),
                patch(
                    "autogame.runtime.process.time.monotonic",
                    side_effect=[0.0, 0.1, 1.0, 1.0],
                ),
                patch("autogame.runtime.process.time.sleep") as sleep,
            ):
                handle = start_process(
                    executable,
                    "MaaEnd.exe",
                    restart_existing=True,
                    allow_existing=False,
                )

        self.assertEqual(handle.pid, 20)
        sleep.assert_called_once_with(0.1)

    def test_start_process_uses_shell_for_gui_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "MaaEnd.exe"
            executable.write_bytes(b"test")
            new_process = Mock(pid=20)
            new_process.create_time.return_value = 100.0

            with (
                patch(
                    "autogame.runtime.process.find_processes",
                    side_effect=[[], [new_process]],
                ),
                patch("autogame.runtime.process.os.startfile") as startfile,
                patch("autogame.runtime.process.subprocess.Popen") as popen,
                patch("autogame.runtime.process.time.time", return_value=100.0),
                patch(
                    "autogame.runtime.process.time.monotonic",
                    side_effect=[0.0, 0.0],
                ),
            ):
                handle = start_process(
                    executable,
                    "MaaEnd.exe",
                    launch_via_shell=True,
                )

        startfile.assert_called_once_with(str(executable))
        popen.assert_not_called()
        self.assertEqual(handle.pid, 20)

    def test_process_cleanup_ignores_pid_that_disappears_while_waiting(self) -> None:
        old_process = Mock(pid=26508)
        old_process.children.return_value = []
        old_process.is_running.side_effect = psutil.NoSuchProcess(26508)

        with patch(
            "autogame.runtime.process.psutil.wait_procs",
            side_effect=psutil.NoSuchProcess(26508),
        ):
            _terminate_process_tree(old_process)

        old_process.terminate.assert_called_once_with()
        old_process.kill.assert_not_called()

    def test_task_log_parsers(self) -> None:
        maa = MaaAdapter().observe_logs([(Path("gui.log"), "任务已全部完成！")])
        self.assertTrue(maa.completion_seen)

        line = (
            '[2026-08-13 19:16:40.389][INF] !!!OnEventNotify!!! '
            '[msg=Tasker.Task.Succeeded] '
            '[details={"entry":"AutoSellMain","task_id":1001,"uuid":"demo"}]'
        )
        maaend_adapter = self._maaend_adapter_with_localized_tasks(
            {
                "AutoSellMain": {
                    "name": "AutoSell",
                    "entry": "AutoSellMain",
                    "label": "$task.AutoSell.label",
                }
            },
            {"task.AutoSell.label": "💰售卖弹性物资"},
        )
        maaend = maaend_adapter.observe_logs(
            [
                (Path("2026-08-17-1.log"), "前段任务已提交, task_ids: [1001]"),
                (Path("maafw.log"), line),
            ]
        )
        self.assertIn(
            "任务完成: 💰售卖弹性物资",
            [item.message for item in maaend.messages],
        )

    def test_maaend_loads_entry_label_from_script_task_definition(self) -> None:
        adapter = self._maaend_adapter_with_localized_tasks(
            {
                "VisitFriendsMain": {
                    "name": "VisitFriends",
                    "entry": "VisitFriendsMain",
                    "label": "$task.VisitFriends.label",
                }
            },
            {"task.VisitFriends.label": "🤝拜访好友"},
        )
        line = (
            '[2026-08-17 19:45:39.847][INF] [msg=Tasker.Task.Failed] '
            '[details={"entry":"VisitFriendsMain","task_id":1001,"uuid":"failed"}]'
        )

        observation = adapter.observe_logs(
            [
                (Path("2026-08-17-1.log"), "前段任务已提交, task_ids: [1001]"),
                (Path("maafw.log"), line),
            ]
        )

        self.assertIn(
            "[ERR] 任务出错: 🤝拜访好友",
            [item.message for item in observation.messages],
        )
        self.assertTrue(observation.completion_seen)

    def test_maaend_waits_for_all_submitted_business_ids(self) -> None:
        adapter = MaaEndAdapter()
        first = adapter.observe_logs(
            [
                (Path("2026-08-17-1.log"), "前段任务已提交, task_ids: [1001,1002]"),
                (
                    Path("maafw.log"),
                    '[msg=Tasker.Task.Succeeded] '
                    '[details={"entry":"VisitFriendsMain","task_id":1001,"uuid":"one"}]',
                ),
            ]
        )
        self.assertFalse(first.completion_seen)

        second = adapter.observe_logs(
            [
                (
                    Path("maafw.log"),
                    '[msg=Tasker.Task.Failed] '
                    '[details={"entry":"DijiangRewards","task_id":1002,"uuid":"two"}]',
                )
            ]
        )
        self.assertTrue(second.completion_seen)
        self.assertIsNone(second.failure_message)

    def test_maaend_manual_stop_is_ignored_by_log_parser(self) -> None:
        adapter = MaaEndAdapter()
        observation = adapter.observe_logs(
            [
                (Path("2026-08-17-1.log"), "前段任务已提交, task_ids: [1001,1002]"),
                (Path("2026-08-17-1.log"), "[task-stop#demo] 停止任务"),
                (
                    Path("maafw.log"),
                    '[msg=Tasker.Task.Succeeded] '
                    '[details={"entry":"VisitFriendsMain","task_id":1001,"uuid":"one"}]',
                ),
            ]
        )
        self.assertFalse(observation.completion_seen)
        self.assertIsNone(observation.failure_message)

    def test_maaend_self_stop_after_business_tasks_is_normal_completion(self) -> None:
        adapter = MaaEndAdapter()
        observation = adapter.observe_logs(
            [
                (
                    Path("2026-08-20-1.log"),
                    "[2026-08-20 07:00:39.000][INF] 前段任务已提交, task_ids: [1001,1002]",
                ),
                (
                    Path("maafw.log"),
                    "[2026-08-20 07:05:00.000][INF] [msg=Tasker.Task.Succeeded] "
                    '[details={"entry":"VisitFriendsMain","task_id":1001,"uuid":"one"}]',
                ),
                (
                    Path("maafw.log"),
                    "[2026-08-20 07:06:00.000][INF] [msg=Tasker.Task.Succeeded] "
                    '[details={"entry":"DijiangRewards","task_id":1002,"uuid":"two"}]',
                ),
                (
                    Path("2026-08-20-1.log"),
                    "[2026-08-20 07:06:01.000][INF] [self-stop#demo] 收到停止自身请求",
                ),
                (
                    Path("2026-08-20-1.log"),
                    "[2026-08-20 07:06:01.000][INF] [task-stop#demo] 停止任务",
                ),
                (
                    Path("maafw.log"),
                    "[2026-08-20 07:06:01.000][INF] [msg=Tasker.Task.Starting] "
                    '[details={"entry":"MXU_KILLPROC","uuid":"control"}]',
                ),
            ]
        )

        self.assertTrue(observation.completion_seen)
        self.assertIsNone(observation.failure_message)
        self.assertNotIn(
            "任务开始: VisitFriendsMain",
            [item.message for item in observation.messages if item.reportable],
        )

    @staticmethod
    def _maaend_adapter_with_localized_tasks(
        task_definitions: dict[str, dict[str, str]],
        locale_data: dict[str, str],
    ) -> MaaEndAdapter:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "MaaEnd.exe"
            executable.write_bytes(b"test")
            (root / "locales" / "interface").mkdir(parents=True)
            (root / "tasks").mkdir()
            (root / "locales" / "interface" / "zh_cn.json").write_text(
                json.dumps(locale_data, ensure_ascii=False),
                encoding="utf-8",
            )
            for task in task_definitions.values():
                (root / "tasks" / f"{task['name']}.json").write_text(
                    json.dumps({"task": [task]}, ensure_ascii=False),
                    encoding="utf-8",
                )
            adapter = MaaEndAdapter()
            adapter._load_entry_names(executable)
            return adapter

    def test_maaend_subtask_error_does_not_mark_whole_run_failed(self) -> None:
        records = [
            (
                Path("maafw.log"),
                '[2026-08-17 19:45:39.847][INF] [msg=Tasker.Task.Failed] '
                '[details={"entry":"VisitFriendsMain","task_id":1001,"uuid":"failed"}]',
            ),
            (
                Path("maafw.log"),
                '[2026-08-17 19:46:12.104][INF] [msg=Tasker.Task.Succeeded] '
                '[details={"entry":"DijiangRewards","task_id":1002,"uuid":"succeeded"}]',
            ),
        ]

        adapter = MaaEndAdapter()
        observation = adapter.observe_logs(
            [(Path("2026-08-17-1.log"), "前段任务已提交, task_ids: [1001,1002]"), *records]
        )

        self.assertIsNone(observation.failure_message)
        self.assertEqual(
            [item.message for item in observation.messages],
            [
                "前段业务任务已提交：2 个",
                "[ERR] 任务出错: VisitFriendsMain",
                "任务完成: DijiangRewards",
                "业务阶段完成：2 个前段任务已进入终态，包含 1 个子任务错误",
            ],
        )

    def test_maa_parser_cleans_errors_without_marking_failure(self) -> None:
        records = [
            (
                Path("gui.log"),
                "[2026-08-14 19:10:26.591][ERR][TaskQueueViewModel] "
                "<2> 任务出错: 理智作战",
            ),
            (
                Path("gui.log"),
                "[2026-08-14 19:10:50.052][INF][TaskQueueViewModel] "
                "<2> 完成任务: 自动公招",
            ),
            (
                Path("gui.log"),
                "[2026-08-14 19:10:50.053][INF][TaskQueueViewModel] "
                "<2> 任务已全部完成！",
            ),
        ]

        observation = MaaAdapter().observe_logs(records)

        self.assertTrue(observation.completion_seen)
        self.assertIsNone(observation.failure_message)
        self.assertIn(
            "[ERR] 任务出错: 理智作战",
            [item.message for item in observation.messages],
        )

    def test_maa_parser_merges_mastery_lines(self) -> None:
        observation = MaaAdapter().observe_logs(
            [
                (
                    Path("gui.log"),
                    "[2026-08-14 19:09:10.605][INF][TaskQueueViewModel] "
                    "<2> [圣聆初雪] 铃音吹雪",
                ),
                (Path("gui.log"), "专精等级: 1 训练完成"),
            ]
        )

        self.assertEqual(
            [item.message for item in observation.messages],
            ["[圣聆初雪] 铃音吹雪，专精等级: 1 训练完成"],
        )

    def test_maaend_end_process_success_is_completion(self) -> None:
        line = (
            '[2026-08-14 19:53:10.263][INF] [msg=Tasker.Task.Succeeded] '
            '[details={"entry":"结束进程","uuid":"finish"}]'
        )

        observation = MaaEndAdapter().observe_logs([(Path("maafw.log"), line)])

        self.assertTrue(observation.completion_seen)

    def test_maaend_mxu_killproc_event_is_not_business_completion(self) -> None:
        line = (
            '[2026-08-17 15:09:46.201][INF] [msg=Tasker.Task.Starting] '
            '[details={"entry":"MXU_KILLPROC","uuid":"finish"}]'
        )

        adapter = MaaEndAdapter()
        observation = adapter.observe_logs([(Path("maafw.log"), line)])

        self.assertTrue(observation.completion_started)
        self.assertFalse(observation.completion_seen)
        self.assertEqual(
            adapter.spec.log_patterns,
            (
                "maafw.log",
                "cpp-algo/debug/maafw.log",
                "????-??-??-*.log",
            ),
        )

    def test_maaend_loads_jsonc_task_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "MaaEnd.exe"
            executable.write_bytes(b"test")
            (root / "locales" / "interface").mkdir(parents=True)
            (root / "tasks").mkdir()
            (root / "locales" / "interface" / "zh_cn.json").write_text(
                '{"task.CreditShopping.label":"🛍️信用点购物"}',
                encoding="utf-8",
            )
            (root / "tasks" / "CreditShopping.json").write_text(
                '{\n'
                '  "task": [{"entry":"CreditShoppingMain",'
                '"label":"$task.CreditShopping.label"}],\n'
                '  "option": {"Exit": {"enabled": false // JSONC 注释\n}}\n'
                '}\n',
                encoding="utf-8",
            )

            adapter = MaaEndAdapter()
            loaded = adapter._load_entry_names(executable)

        self.assertEqual(loaded, 1)
        self.assertEqual(adapter._entry_names["CreditShoppingMain"], "🛍️信用点购物")

    def test_maaend_ignores_framework_enter_leave_trace_lines(self) -> None:
        observation = MaaEndAdapter().observe_logs(
            [
                (
                    Path("maafw.log"),
                    "[CreditShopping检查物资] | enter",
                ),
                (
                    Path("maafw.log"),
                    "[CreditShopping检查物资] | leave, 1ms",
                ),
            ]
        )

        self.assertEqual(observation.messages, ())

class ProcessAdapterTests(unittest.IsolatedAsyncioTestCase):
    """验证外部任务的日志静默判定。"""

    async def test_maa_start_only_restarts_existing_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "MAA.exe"
            script_path.write_bytes(b"test")
            context = TaskContext(
                "maa",
                TaskConfig(enabled=True, script_path=str(script_path)),
                datetime.now(timezone.utc),
            )
            script_handle = ProcessHandle(101, "MAA.exe", 2.0, True)
            start_process = AsyncMock(return_value=script_handle)

            with patch(
                "autogame.tasks.core.process_script.start_process_async",
                new=start_process,
            ):
                result = await MaaAdapter().start(context)

        self.assertEqual(result.result.state, "running")
        self.assertTrue(result.result.waiting_for_completion)
        self.assertIsInstance(result.handle, ProcessRun)
        assert isinstance(result.handle, ProcessRun)
        start_process.assert_awaited_once_with(
            script_path,
            "MAA.exe",
            15.0,
            allow_existing=False,
            restart_existing=True,
            launch_via_shell=False,
        )

    async def test_maaend_start_only_starts_and_restarts_existing_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "MaaEnd.exe"
            script_path.write_bytes(b"test")
            context = TaskContext(
                "maaend",
                TaskConfig(enabled=True, script_path=str(script_path)),
                datetime.now(timezone.utc),
            )
            script_handle = ProcessHandle(200, "MaaEnd.exe", 2.0, True)
            start_process = AsyncMock(return_value=script_handle)

            with patch(
                "autogame.tasks.core.process_script.start_process_async",
                new=start_process,
            ):
                result = await MaaEndAdapter().start(context)

        self.assertEqual(result.result.state, "running")
        self.assertTrue(result.result.waiting_for_completion)
        self.assertIsInstance(result.handle, ProcessRun)
        start_process.assert_awaited_once_with(
            script_path,
            "MaaEnd.exe",
            15.0,
            allow_existing=False,
            restart_existing=True,
            launch_via_shell=False,
        )

    async def test_maaend_fails_after_five_minutes_of_log_inactivity(self) -> None:
        class EmptyReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return []

        adapter = MaaEndAdapter()
        adapter.observe_logs(
            [
                (Path("2026-08-22-1.log"), "前段任务已提交, task_ids: [1001,1002]"),
                (
                    Path("maafw.log"),
                    '[msg=Tasker.Task.Succeeded] '
                    '[details={"entry":"VisitFriendsMain","task_id":1001,"uuid":"one"}]',
                ),
            ]
        )
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MaaEnd.exe", 1.0, True),
            log_reader=EmptyReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
            activity_seen=True,
            last_meaningful_activity_at=0,
        )
        context = TaskContext("maaend", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with (
            patch("autogame.tasks.core.process_script.time.monotonic", return_value=300),
            patch("autogame.tasks.core.process_script.process_is_running", return_value=True),
            patch("autogame.tasks.core.process_script.stop_process_tree") as stop_process,
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "failed")
        self.assertIn("前段业务任务未全部进入终态", result.message or "")
        stop_process.assert_called_once_with(handle.script_process)

    async def test_maaend_fails_after_five_minutes_without_business_logs(self) -> None:
        class EmptyReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return []

        adapter = MaaEndAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MaaEnd.exe", 1.0, True),
            log_reader=EmptyReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
        )
        context = TaskContext("maaend", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with (
            patch("autogame.tasks.core.process_script.time.monotonic", return_value=300),
            patch("autogame.tasks.core.process_script.process_is_running", return_value=True),
            patch("autogame.tasks.core.process_script.stop_process_tree") as stop_process,
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "failed")
        self.assertIn("连续 5 分钟无有效业务日志", result.message or "")
        stop_process.assert_called_once_with(handle.script_process)

    async def test_maaend_waits_through_startup_pid_switch(self) -> None:
        class EmptyReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return []

        adapter = MaaEndAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MaaEnd.exe", 1.0, True),
            log_reader=EmptyReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
        )
        context = TaskContext(
            "maaend",
            TaskConfig(enabled=True),
            datetime.now(timezone.utc),
        )

        with (
            patch("autogame.tasks.core.process_script.time.monotonic", return_value=1),
            patch(
                "autogame.tasks.core.process_script.process_is_running",
                return_value=False,
            ),
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "running")
        self.assertTrue(handle.startup_grace_logged)

    async def test_maaend_process_exit_after_control_start_is_completion(self) -> None:
        class ControlReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return [
                    (
                        Path("maafw.log"),
                        '[msg=Tasker.Task.Starting] '
                        '[details={"entry":"MXU_KILLPROC","task_id":2001}]',
                    )
                ]

        adapter = MaaEndAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MaaEnd.exe", 1.0, True),
            log_reader=ControlReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
        )
        context = TaskContext("maaend", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with (
            patch("autogame.tasks.core.process_script.time.monotonic", return_value=20),
            patch(
                "autogame.tasks.core.process_script.process_is_running",
                return_value=False,
            ),
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "completed")
        self.assertIn("结束进程任务启动后脚本退出", result.message or "")

    async def test_maa_error_log_does_not_change_completed_result(self) -> None:
        class MaaReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return [
                    (Path("gui.log"), "任务出错: 理智作战"),
                    (Path("gui.log"), "完成任务: 自动公招"),
                    (Path("gui.log"), "任务已全部完成！"),
                ]

        adapter = MaaAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MAA.exe", 1.0, True),
            log_reader=MaaReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
        )
        context = TaskContext("maa", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with patch(
            "autogame.tasks.core.process_script.process_is_running",
            return_value=True,
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "completed")
        self.assertIn("[ERR] 任务出错: 理智作战", result.report_lines)

    async def test_maa_all_error_logs_mark_run_failed(self) -> None:
        class MaaReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return [
                    (Path("gui.log"), "任务出错: 理智作战"),
                    (Path("gui.log"), "任务出错: 仓库识别"),
                    (Path("gui.log"), "任务已全部完成！"),
                ]

        adapter = MaaAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MAA.exe", 1.0, True),
            log_reader=MaaReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
        )
        context = TaskContext("maa", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with patch(
            "autogame.tasks.core.process_script.process_is_running",
            return_value=True,
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "failed")
        self.assertEqual(result.message, "整轮任务失败：所有实际执行任务均报错")
        self.assertIn("[ERR] 任务出错: 理智作战", result.report_lines)
