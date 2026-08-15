"""MaaEnd 任务适配器和详细业务日志解析器。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from autogame.tasks.process_script import (
    LogObservation,
    ProcessScriptAdapter,
    ProcessScriptSpec,
    TaskLogLine,
)


class MaaEndAdapter(ProcessScriptAdapter):
    """启动终末地和 MaaEnd，以结束进程任务或日志静默作为完成信号。"""

    description = "监听 MaaEnd 业务日志，检测结束进程任务或日志静默"

    _entry_names = {  # noqa: RUF012
        "AutoSellMain": "💰售卖弹性物资",
        "SellProductSchedule": "🛒售卖产品",
        "AutoStockpileMain": "📦自动囤货",
        "AutoStockStapleSchedule": "🏪购买稳定物资",
        "EnvironmentMonitoringMain": "🌿环境监测",
        "EnvironmentMonitoringSchedule": "🌿环境监测",
        "DailyRewardStart": "📅日常奖励领取",
        "DailyRewardsStart": "📅日常奖励领取",
        "DailyRewardsMain": "📅日常奖励领取",
        "AutoCollectSchedule": "自动收集计划",
        "EndProcess": "结束进程",
        "ExitProcess": "结束进程",
    }
    _completion_entries = {"结束进程", "EndProcess", "ExitProcess", "CloseProcess"}  # noqa: RUF012

    def __init__(self) -> None:
        super().__init__(
            ProcessScriptSpec(
                process_name="MaaEnd.exe",
                game_path=r"D:\game\Hypergryph Launcher\games\Arknights Endfield\Endfield.exe",
                game_process_name="Endfield.exe",
                log_patterns=("maafw.log", "maafw*.log", "go-service.log"),
                completion_mode="log_marker",
                game_ready_delay_seconds=20,
                inactivity_completion_seconds=10 * 60,
            )
        )

    def observe_logs(self, records: list[tuple[Path, str]]) -> LogObservation:
        """把 MaaEnd 事件日志转换为可读的任务进度。"""

        activity = False
        failure: str | None = None
        completion_started = False
        completed = False
        messages: list[TaskLogLine] = []

        for _, line in records:
            event = re.search(r"Tasker\.Task\.(Starting|Succeeded|Failed).*?details=(\{.*\})", line)
            if event:
                action, payload = event.groups()
                try:
                    details = json.loads(payload)
                except json.JSONDecodeError:
                    details = {}
                entry = str(details.get("entry", "未知任务"))
                label = self._entry_names.get(entry, entry)
                event_key = f"{action}:{entry}:{details.get('uuid', line)}"
                is_completion_entry = (
                    entry in self._completion_entries
                    or "结束进程" in entry
                    or "结束进程" in label
                )
                activity = True
                if action == "Starting":
                    completion_started |= is_completion_entry
                    messages.append(TaskLogLine(event_key, f"任务开始: {label}"))
                elif action == "Succeeded":
                    completed |= is_completion_entry
                    messages.append(TaskLogLine(event_key, f"任务完成: {label}"))
                else:
                    failure = f"任务失败: {label}"
                    messages.append(TaskLogLine(event_key, failure))
                continue

            if self._is_detail_line(line):
                activity = True
                messages.append(
                    TaskLogLine(line, self._clean_line(line), reportable=False)
                )

        return LogObservation(
            activity_seen=activity,
            completion_started=completion_started,
            completion_seen=completed,
            failure_message=failure,
            messages=tuple(messages),
        )

    @staticmethod
    def _is_detail_line(line: str) -> bool:
        """判断是否为用户关心的业务过程日志。"""

        return any(
            keyword in line
            for keyword in (
                "检查物资",
                "不匹配",
                "价格变动较大",
                "任务开始:",
                "任务完成:",
            )
        ) or bool(re.search(r"\d+\s*>=\s*\d+", line))

    @staticmethod
    def _clean_line(line: str) -> str:
        """去除底层日志前缀，保留业务文本。"""

        parts = line.rsplit("] ", 1)
        return parts[-1].strip()
