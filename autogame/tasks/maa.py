"""MAA 任务适配器。"""

from __future__ import annotations

import re
from pathlib import Path

from autogame.tasks.process_script import (
    LogObservation,
    ProcessScriptAdapter,
    ProcessScriptSpec,
    TaskLogLine,
)


class MaaAdapter(ProcessScriptAdapter):
    """启动模拟器和 MAA，并以 gui.log 的整轮完成文本作为完成标志。"""

    description = "监听 MAA 的 gui.log，读取到任务已全部完成后结束"

    _prefix_pattern = re.compile(
        r"^\[[^\]]+\]\[[^\]]+\]\[[^\]]+\]\s*<[^>]+>\s*(?P<message>.*)$"
    )

    def __init__(self) -> None:
        super().__init__(
            ProcessScriptSpec(
                process_name="MAA.exe",
                game_path=r"D:\OneDrive\win\桌面\#1 MuMu安卓设备-1.lnk",
                game_process_name="MuMuNxDevice.exe",
                log_patterns=("gui.log",),
                completion_mode="log_marker",
                game_ready_delay_seconds=20,
            )
        )
        self._previous_message: str | None = None

    def observe_logs(self, records: list[tuple[Path, str]]) -> LogObservation:
        """清洗并筛选任务结果和专精等级信息。"""

        messages: list[TaskLogLine] = []
        completed = False
        for path, line in records:
            message = self._clean_message(line)
            key = f"{path.name}:{line}"
            if "任务已全部完成" in message or "All tasks completed" in message:
                completed = True
                messages.append(TaskLogLine(key, "任务已全部完成"))
            elif "任务出错" in message:
                detail = self._extract_detail(message, "任务出错")
                messages.append(TaskLogLine(key, f"[ERR] 任务出错: {detail}"))
            elif "任务跳过" in message:
                messages.append(TaskLogLine(key, f"[SKIP] {message}"))
            elif "完成任务" in message:
                messages.append(TaskLogLine(key, message))
            elif "专精等级" in message:
                mastery = message
                if self._previous_message and "专精等级" not in self._previous_message:
                    mastery = f"{self._previous_message}，{message}"
                messages.append(TaskLogLine(key, mastery))
            self._previous_message = message

        return LogObservation(
            activity_seen=bool(messages),
            completion_seen=completed,
            messages=tuple(messages),
        )

    @classmethod
    def _clean_message(cls, line: str) -> str:
        """移除 MAA 自带的时间、级别、组件和线程前缀。"""

        stripped = line.strip()
        match = cls._prefix_pattern.match(stripped)
        if match:
            return match.group("message").strip()
        return stripped

    @staticmethod
    def _extract_detail(message: str, marker: str) -> str:
        """提取任务标记后的说明并统一冒号格式。"""

        detail = message.split(marker, 1)[1].lstrip("：: ")
        return detail or "未提供错误说明"
