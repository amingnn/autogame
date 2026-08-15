"""使用三个任务的真实日志样本展示整轮自动化通知。"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# 直接运行测试文件时，允许导入项目代码和相邻测试样本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
for search_path in (PROJECT_ROOT, TEST_ROOT):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

# 摘自 2026-08-14 最近一次森空岛重复签到日志。
SKYLAND_SIGN_REPEATED = (
    (
        "2026-08-14 15:56:27.500",
        "[明日方舟]角色阿铭#5977(官服)签到失败了！原因：请勿重复签到！",
    ),
    (
        "2026-08-14 15:56:27.677",
        "[明日方舟：终末地]角色Aming(官服)签到失败了！原因:请勿重复签到！",
    ),
)

# MAA 最近一轮 gui.log 原始片段定义在单任务输出测试中。
from test_maa_output import MAA_RAW_LOG as MAA_RECENT_LOG

# 摘自最新一轮含业务事件的 MaaEnd 框架日志：
# maafw.bak.2026.08.14-19.53.06.792.log
MAAEND_RECENT_LOG = """[2026-08-14 19:17:24.543][INF][Px36196][Tx2471][Utils/EventDispatcher.hpp][L65][MaaNS::EventDispatcher::notify] !!!OnEventNotify!!! [handle=true] [msg=Tasker.Task.Starting] [details={"entry":"EnvironmentMonitoringMain","hash":"dc281c00a9d17e86","task_id":200000009,"uuid":"00000000007E0F1A"}]
[2026-08-14 19:21:43.820][INF][Px36196][Tx2471][Utils/EventDispatcher.hpp][L65][MaaNS::EventDispatcher::notify] !!!OnEventNotify!!! [handle=true] [msg=Tasker.Task.Succeeded] [details={"entry":"EnvironmentMonitoringMain","hash":"dc281c00a9d17e86","task_id":200000009,"uuid":"00000000007E0F1A"}]
[2026-08-14 19:21:43.821][INF][Px36196][Tx2471][Utils/EventDispatcher.hpp][L65][MaaNS::EventDispatcher::notify] !!!OnEventNotify!!! [handle=true] [msg=Tasker.Task.Starting] [details={"entry":"DailyRewardStart","hash":"dc281c00a9d17e86","task_id":200000010,"uuid":"00000000007E0F1A"}]
[2026-08-14 19:23:34.175][INF][Px36196][Tx2471][Utils/EventDispatcher.hpp][L65][MaaNS::EventDispatcher::notify] !!!OnEventNotify!!! [handle=true] [msg=Tasker.Task.Succeeded] [details={"entry":"DailyRewardStart","hash":"dc281c00a9d17e86","task_id":200000010,"uuid":"00000000007E0F1A"}]
[2026-08-14 19:23:34.176][INF][Px36196][Tx2471][Utils/EventDispatcher.hpp][L65][MaaNS::EventDispatcher::notify] !!!OnEventNotify!!! [handle=true] [msg=Tasker.Task.Starting] [details={"entry":"AutoCollectSchedule","hash":"dc281c00a9d17e86","task_id":200000011,"uuid":"00000000007E0F1A"}]
[2026-08-14 19:53:04.913][INF][Px36196][Tx2471][Utils/EventDispatcher.hpp][L65][MaaNS::EventDispatcher::notify] !!!OnEventNotify!!! [handle=true] [msg=Tasker.Task.Failed] [details={"entry":"AutoCollectSchedule","hash":"dc281c00a9d17e86","task_id":200000011,"uuid":"00000000007E0F1A"}]"""

from autogame.automation.runner import AutomationRunner
from autogame.config import AppPaths, Config, SystemConfig, TaskConfig
from autogame.notify import build_push_payload
from autogame.tasks.maa import MaaAdapter
from autogame.tasks.maaend import MaaEndAdapter
from autogame.tasks.process_script import TaskLogLine


TIMESTAMP_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]"
)


class FullNotificationOutputTests(unittest.TestCase):
    """验证三个任务组合后的日志文件内容和通知正文。"""

    def test_three_task_notification_preview(self) -> None:
        maa_records = self._records("gui.log", MAA_RECENT_LOG)
        maa_observation = MaaAdapter().observe_logs(maa_records)
        maa_lines = self._report_lines(maa_observation.messages)

        maaend_records = self._records("maafw.log", MAAEND_RECENT_LOG)
        maaend_observation = MaaEndAdapter().observe_logs(maaend_records)
        maaend_lines = self._report_lines(maaend_observation.messages)

        project_log_lines = [
            *[
                f"{timestamp} | INFO     | skyland_sign | {message}"
                for timestamp, message in SKYLAND_SIGN_REPEATED
            ],
            *self._format_maa_loguru_lines(maa_records, maa_observation.messages),
            *self._format_sequential_loguru_lines(
                "maaend",
                maaend_records,
                maaend_lines,
            ),
        ]

        notification_sections = self._build_notification_sections(
            SKYLAND_SIGN_REPEATED,
            maa_lines,
            maaend_lines,
            maaend_observation.failure_message,
        )
        notification_payload = build_push_payload(notification_sections)

        if __name__ == "__main__":
            self._print_preview(project_log_lines, notification_payload)

        self.assertTrue(maa_observation.completion_seen)
        self.assertIsNone(maa_observation.failure_message)
        self.assertEqual(
            maaend_observation.failure_message,
            "任务失败: 自动收集计划",
        )
        self.assertEqual(notification_payload["title"], "自动化任务报告")
        self.assertEqual(len(notification_sections), 4)
        self.assertEqual(notification_payload["desp"].count("```"), 8)
        self.assertTrue(notification_payload["desp"].startswith("```\n"))
        self.assertTrue(notification_payload["desp"].endswith("\n```"))
        self.assertIn("任务总结\n任务列表：", notification_payload["desp"])
        self.assertIn(
            "- skyland_sign：完成（用时 0 分 1 秒）",
            notification_payload["desp"],
        )
        self.assertIn("请勿重复签到", notification_payload["desp"])
        self.assertIn(
            "- maa：完成（用时 10 分 50 秒）",
            notification_payload["desp"],
        )
        self.assertIn(
            "- maaend：失败（用时 51 分 31 秒）",
            notification_payload["desp"],
        )
        self.assertIn(
            "skyland_sign：完成 (0 分 1 秒)",
            notification_payload["desp"],
        )
        self.assertIn("maa：完成 (10 分 50 秒)", notification_payload["desp"])
        self.assertIn("maaend：失败 (51 分 31 秒)", notification_payload["desp"])
        self.assertIn("总用时：51 分 31 秒", notification_payload["desp"])
        self.assertIn(
            "完成后动作：hibernate，延迟 60 秒",
            notification_payload["desp"],
        )

    def test_summary_includes_cooldown_and_disabled_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                tasks={
                    "maa": TaskConfig(enabled=True),
                    "maaend": TaskConfig(enabled=True),
                    "skyland_sign": TaskConfig(enabled=False),
                },
            )
            sections = AutomationRunner(config)._build_report_sections(
                [
                    {
                        "name": "maa",
                        "state": "completed",
                        "elapsed_seconds": 650.2,
                        "error": None,
                        "lines": [],
                    }
                ],
                elapsed_seconds=651,
                timed_out=False,
                completion_action_enabled=False,
            )

        self.assertEqual(
            sections[0],
            "\n".join(
                [
                    "任务总结",
                    "任务列表：",
                    "- maa：完成（用时 10 分 50 秒）",
                    "- maaend：冷却中",
                    "- skyland_sign：已关闭",
                    "总用时：10 分 51 秒",
                    "完成后动作：未执行（配置为 none）",
                ]
            ),
        )

    @staticmethod
    def _records(file_name: str, raw_log: str) -> list[tuple[Path, str]]:
        return [(Path(file_name), line) for line in raw_log.splitlines()]

    @staticmethod
    def _report_lines(messages: tuple[TaskLogLine, ...]) -> list[str]:
        return [item.message for item in messages if item.reportable]

    @staticmethod
    def _format_maa_loguru_lines(
        records: list[tuple[Path, str]],
        messages: tuple[TaskLogLine, ...],
    ) -> list[str]:
        timestamps: dict[str, str] = {}
        current_timestamp = "0000-00-00 00:00:00.000"
        for _, line in records:
            match = TIMESTAMP_PATTERN.match(line)
            if match:
                current_timestamp = match.group(1)
            timestamps[line] = current_timestamp

        return [
            f"{timestamps[item.key.partition(':')[2]]} | INFO     | maa | "
            f"{item.message}"
            for item in messages
            if item.reportable
        ]

    @staticmethod
    def _format_sequential_loguru_lines(
        task_name: str,
        records: list[tuple[Path, str]],
        messages: list[str],
    ) -> list[str]:
        timestamps = [
            match.group(1)
            for _, line in records
            if (match := TIMESTAMP_PATTERN.match(line))
        ]
        return [
            f"{timestamp} | INFO     | {task_name} | {message}"
            for timestamp, message in zip(timestamps, messages, strict=True)
        ]

    @staticmethod
    def _build_notification_sections(
        skyland_messages: tuple[tuple[str, str], ...],
        maa_lines: list[str],
        maaend_lines: list[str],
        maaend_error: str | None,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(
                    completion_action="hibernate",
                    completion_action_delay_seconds=60,
                ),
                tasks={
                    "skyland_sign": TaskConfig(enabled=True),
                    "maa": TaskConfig(enabled=True),
                    "maaend": TaskConfig(enabled=True),
                },
            )
            runner = AutomationRunner(config)
            results: list[dict[str, object]] = [
                {
                    "name": "skyland_sign",
                    "state": "completed",
                    "elapsed_seconds": 1.4,
                    "error": None,
                    "lines": [message for _, message in skyland_messages],
                },
                {
                    "name": "maa",
                    "state": "completed",
                    "elapsed_seconds": 650.2,
                    "error": None,
                    "lines": maa_lines,
                },
                {
                    "name": "maaend",
                    "state": "failed",
                    "elapsed_seconds": 3090.6,
                    "error": maaend_error,
                    "lines": maaend_lines,
                },
            ]
            return runner._build_report_sections(
                results,
                elapsed_seconds=3091,
                timed_out=False,
                completion_action_enabled=True,
            )

    @staticmethod
    def _print_preview(
        project_log_lines: list[str],
        notification_payload: dict[str, str],
    ) -> None:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
        console = Console(
            width=140,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        console.print(
            Panel(
                Text("\n".join(project_log_lines)),
                title="项目日志预览（Loguru 文件格式）",
                border_style="bright_green",
            )
        )
        console.print(
            Panel(
                Text(notification_payload["desp"]),
                title=f"Notify 通知预览｜{notification_payload['title']}",
                border_style="bright_blue",
            )
        )


if __name__ == "__main__":
    unittest.main()
