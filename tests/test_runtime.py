"""测试进程之外的状态、锁和日志读取能力。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from autogame.config import TaskConfig
from autogame.runtime.execution_lock import ExecutionLock
from autogame.runtime.log_reader import IncrementalLogReader
from autogame.runtime.process import ProcessHandle, find_processes, start_process
from autogame.runtime.state_store import StateStore
from autogame.tasks.base import TaskContext
from autogame.tasks.maa import MaaAdapter
from autogame.tasks.maaend import MaaEndAdapter
from autogame.tasks.process_script import ProcessRun


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
                    side_effect=[0.0, 0.0],
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

    def test_task_log_parsers(self) -> None:
        maa = MaaAdapter().observe_logs([(Path("gui.log"), "任务已全部完成！")])
        self.assertTrue(maa.completion_seen)

        line = (
            '[2026-08-13 19:16:40.389][INF] !!!OnEventNotify!!! '
            '[msg=Tasker.Task.Succeeded] '
            '[details={"entry":"AutoSellMain","uuid":"demo"}]'
        )
        maaend = MaaEndAdapter().observe_logs([(Path("maafw.log"), line)])
        self.assertIn(
            "任务完成: 💰售卖弹性物资",
            [item.message for item in maaend.messages],
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

    def test_maaend_end_process_event_is_completion_marker(self) -> None:
        line = (
            '[2026-08-14 19:53:10.263][INF] [msg=Tasker.Task.Succeeded] '
            '[details={"entry":"结束进程","uuid":"finish"}]'
        )

        observation = MaaEndAdapter().observe_logs([(Path("maafw.log"), line)])

        self.assertTrue(observation.completion_seen)


class ProcessAdapterTests(unittest.IsolatedAsyncioTestCase):
    """验证外部任务的日志静默完成兜底。"""

    async def test_maaend_completes_after_meaningful_log_inactivity(self) -> None:
        class EmptyReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return []

        adapter = MaaEndAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MaaEnd.exe", 1.0, True),
            game_process=None,
            log_reader=EmptyReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
            activity_seen=True,
            last_meaningful_activity_at=0,
        )
        context = TaskContext("maaend", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with (
            patch("autogame.tasks.process_script.time.monotonic", return_value=601),
            patch("autogame.tasks.process_script.process_is_running", return_value=True),
            patch("autogame.tasks.process_script.stop_process_tree") as stop_process,
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "completed")
        self.assertIn("连续 10 分钟无有效业务日志", result.report_lines[-1])
        stop_process.assert_called_once()

    async def test_maa_error_log_does_not_change_completed_result(self) -> None:
        class MaaReader:
            def read_lines(self) -> list[tuple[Path, str]]:
                return [
                    (Path("gui.log"), "任务出错: 理智作战"),
                    (Path("gui.log"), "任务已全部完成！"),
                ]

        adapter = MaaAdapter()
        handle = ProcessRun(
            script_process=ProcessHandle(1, "MAA.exe", 1.0, True),
            game_process=None,
            log_reader=MaaReader(),  # type: ignore[arg-type]
            started_at_monotonic=0,
        )
        context = TaskContext("maa", TaskConfig(enabled=True), datetime.now(timezone.utc))

        with patch(
            "autogame.tasks.process_script.process_is_running",
            return_value=True,
        ):
            result = await adapter.poll(context, handle)

        self.assertEqual(result.state, "completed")
        self.assertIn("[ERR] 任务出错: 理智作战", result.report_lines)
