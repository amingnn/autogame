"""直观展示并验证 MAA 日志清洗结果与自动化通知正文。"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

# 直接运行测试文件时，将项目根目录加入模块搜索路径。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 该片段原封不动摘自 2026-08-14 的 MAA gui.log。
MAA_RAW_LOG = """[2026-08-14 19:01:43.396][INF][TaskQueueViewModel]     <2> 更新数据: 更新数据 任务跳过
[2026-08-14 19:01:43.407][INF][TaskQueueViewModel]     <2> 正在运行中……
[2026-08-14 19:01:43.421][INF][AsstProxy]              <2> Start Task Chain: StartUp, Task ID: 1
[2026-08-14 19:01:43.465][INF][TaskQueueViewModel]     <2> LinkStart Exit, 11,856 ms
[2026-08-14 19:05:07.238][INF][TaskQueueViewModel]     <2> 完成任务: 开始唤醒
[2026-08-14 19:05:07.283][INF][AsstProxy]              <2> Completed Task Chain: StartUp, Task ID: 1
[2026-08-14 19:05:08.928][INF][AsstProxy]              <2> Start Task Chain: Recruit, Task ID: 2
[2026-08-14 19:05:20.176][INF][TaskQueueViewModel]     <2> 完成任务: 自动公招
[2026-08-14 19:05:20.412][INF][AsstProxy]              <2> Completed Task Chain: Recruit, Task ID: 2
[2026-08-14 19:05:21.194][INF][AsstProxy]              <2> Start Task Chain: Infrast, Task ID: 3
[2026-08-14 19:05:53.176][INF][TaskQueueViewModel]     <2> 当前设施: 制造站 01
[2026-08-14 19:06:25.554][INF][TaskQueueViewModel]     <2> 当前设施: 制造站 01
[2026-08-14 19:06:40.724][INF][TaskQueueViewModel]     <2> 当前设施: 制造站 01
[2026-08-14 19:06:52.809][INF][TaskQueueViewModel]     <2> 当前设施: 制造站 02
[2026-08-14 19:06:57.321][INF][TaskQueueViewModel]     <2> 当前设施: 制造站 03
[2026-08-14 19:07:02.081][INF][TaskQueueViewModel]     <2> 当前设施: 制造站 04
[2026-08-14 19:07:18.269][INF][TaskQueueViewModel]     <2> 当前设施: 贸易站 01
[2026-08-14 19:07:44.658][INF][TaskQueueViewModel]     <2> 当前设施: 贸易站 02
[2026-08-14 19:07:58.189][INF][TaskQueueViewModel]     <2> 当前设施: 会客室 01
[2026-08-14 19:08:51.460][INF][TaskQueueViewModel]     <2> 当前设施: 训练室 01
[2026-08-14 19:09:10.605][INF][TaskQueueViewModel]     <2> [圣聆初雪] 铃音吹雪
专精等级: 1 训练完成
[2026-08-14 19:09:24.964][INF][TaskQueueViewModel]     <2> 完成任务: 基建换班
[2026-08-14 19:09:25.034][INF][AsstProxy]              <2> Completed Task Chain: Infrast, Task ID: 3
[2026-08-14 19:09:25.403][INF][AsstProxy]              <2> Start Task Chain: Mall, Task ID: 4
[2026-08-14 19:09:39.636][INF][TaskQueueViewModel]     <2> 完成任务: 信用收支
[2026-08-14 19:09:39.639][INF][AsstProxy]              <2> Completed Task Chain: Mall, Task ID: 4
[2026-08-14 19:09:40.159][INF][AsstProxy]              <2> Start Task Chain: Fight, Task ID: 5
[2026-08-14 19:10:19.771][INF][TaskQueueViewModel]     <2> 完成任务: 剿灭
[2026-08-14 19:10:20.051][INF][AsstProxy]              <2> Completed Task Chain: Fight, Task ID: 5
[2026-08-14 19:10:20.828][INF][AsstProxy]              <2> Start Task Chain: Fight, Task ID: 6
[2026-08-14 19:10:26.591][ERR][TaskQueueViewModel]     <2> 任务出错: 理智作战
[2026-08-14 19:10:27.353][INF][AsstProxy]              <2> Start Task Chain: Award, Task ID: 7
[2026-08-14 19:10:31.115][WRN][HttpResponseLoggingExtension] <2> HTTP: BadGateway POST http://127.0.0.1:8000/maa null null 3868.851ms
[2026-08-14 19:10:31.188][WRN][CustomWebhookNotificationProvider] <2> Custom Webhook failed to send: Response status code does not indicate success: 502 (Bad Gateway).
[2026-08-14 19:10:48.191][ERR][TaskQueueViewModel]     <2> 任务出错: 领取奖励
[2026-08-14 19:10:49.810][INF][ConfigurationHelper]    <2> Configuration Root.Configurations.Default.[5]活动关(FightTask).StagePlan has been set: `["TO-9"]` -> `[""]`, save scheduled
[2026-08-14 19:10:49.857][INF][ConfigurationHelper]    <2> Configuration Root.Configurations.Default.[5]活动关(FightTask).StagePlan has been set: `[""]` -> `["TO-9"]`, save scheduled
[2026-08-14 19:10:49.994][INF][RunningState]           <2> Idle: false to true (called from ProcTaskChainMsg)
[2026-08-14 19:10:50.052][INF][TaskQueueViewModel]     <2> 任务已全部完成！
(用时 0h 9m 6s)"""

from autogame.automation.runner import AutomationRunner
from autogame.config import AppPaths, Config, SystemConfig
from autogame.tasks.maa import MaaAdapter
from autogame.tasks.process_script import TaskLogLine


class MaaOutputTests(unittest.TestCase):
    """使用 MAA 真实格式日志验证面向用户的最终输出。"""

    def test_cleaned_logs_and_notification_message(self) -> None:
        records = [(Path("gui.log"), line) for line in MAA_RAW_LOG.splitlines()]

        observation = MaaAdapter().observe_logs(records)
        cleaned_lines = [
            item.message for item in observation.messages if item.reportable
        ]
        loguru_lines = self._format_loguru_lines(records, observation.messages)
        notification = self._build_notification(cleaned_lines)

        self._print_outputs(loguru_lines, notification)

        self.assertTrue(observation.completion_seen)
        self.assertIsNone(observation.failure_message)
        self.assertEqual(
            cleaned_lines,
            [
                "[SKIP] 更新数据: 更新数据 任务跳过",
                "完成任务: 开始唤醒",
                "完成任务: 自动公招",
                "[圣聆初雪] 铃音吹雪，专精等级: 1 训练完成",
                "完成任务: 基建换班",
                "完成任务: 信用收支",
                "完成任务: 剿灭",
                "[ERR] 任务出错: 理智作战",
                "[ERR] 任务出错: 领取奖励",
                "任务已全部完成",
            ],
        )
        self.assertEqual(
            loguru_lines[0],
            "2026-08-14 19:01:43.396 | INFO     | maa | "
            "[SKIP] 更新数据: 更新数据 任务跳过",
        )
        self.assertIn("- maa：完成", notification)
        self.assertIn("maa：完成 (10 分 50 秒)", notification)
        self.assertIn("[ERR] 任务出错: 理智作战", notification)
        self.assertIn("[ERR] 任务出错: 领取奖励", notification)
        self.assertIn("任务已全部完成", notification)
        self.assertIn("总用时：10 分 51 秒", notification)
        self.assertIn("完成后动作：hibernate，延迟 60 秒", notification)

    @staticmethod
    def _build_notification(cleaned_lines: list[str]) -> str:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(
                    completion_action="hibernate",
                    completion_action_delay_seconds=60,
                ),
            )
            runner = AutomationRunner(config)
            sections = runner._build_report_sections(
                [
                    {
                        "name": "maa",
                        "state": "completed",
                        "elapsed_seconds": 650.2,
                        "error": None,
                        "lines": cleaned_lines,
                    }
                ],
                elapsed_seconds=651,
                timed_out=False,
                completion_action_enabled=True,
            )
            return "\n\n".join(sections)

    @staticmethod
    def _format_loguru_lines(
        records: list[tuple[Path, str]],
        messages: tuple[TaskLogLine, ...],
    ) -> list[str]:
        timestamps: dict[str, str] = {}
        current_timestamp = "0000-00-00 00:00:00.000"
        timestamp_pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
        for _, line in records:
            match = timestamp_pattern.match(line)
            if match:
                current_timestamp = match.group(1)
            timestamps[line] = current_timestamp

        result: list[str] = []
        for item in messages:
            if not item.reportable:
                continue
            source_line = item.key.partition(":")[2]
            result.append(
                f"{timestamps[source_line]} | INFO     | maa | {item.message}"
            )
        return result

    @staticmethod
    def _print_outputs(loguru_lines: list[str], notification: str) -> None:
        console = Console(width=120)
        console.print("\n")
        console.print(
            Panel(
                "\n".join(loguru_lines),
                title="项目日志文件（Loguru 格式）",
                border_style="green",
            )
        )

        console.print(Panel(notification, title="Notify 通知正文", border_style="blue"))


if __name__ == "__main__":
    unittest.main()
