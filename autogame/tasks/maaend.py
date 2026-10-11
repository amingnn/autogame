"""MaaEnd 任务适配器和详细业务日志解析器。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from autogame.logger import get_task_logger
from autogame.tasks.core.base import StartResult, TaskContext
from autogame.tasks.core.process_script import (
    LogObservation,
    ProcessScriptAdapter,
    ProcessScriptSpec,
    TaskLogLine,
)


class MaaEndAdapter(ProcessScriptAdapter):
    """启动 MaaEnd，以前段业务任务 ID 全部终态作为完成信号。"""

    description = "监听 MaaEnd 业务日志，等待任务完成；连续 5 分钟无日志判定失败"

    _control_entry_names = {  # noqa: RUF012
        "EndProcess": "结束进程",
        "ExitProcess": "结束进程",
        "CloseProcess": "结束进程",
        "MXU_KILLPROC": "结束进程",
        "__MXU_KILLPROC__": "结束进程",
    }
    _primary_task_ids_pattern = re.compile(
        r"前段任务已提交.*?(?:task_ids|taskIds)\s*:\s*\[([0-9,\s]+)\]"
    )
    _task_event_pattern = re.compile(
        r"Tasker\.Task\.(Starting|Succeeded|Failed).*?details=(\{.*\})"
    )
    _timestamp_pattern = re.compile(
        r"(?:^|\[)(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    )

    def __init__(self) -> None:
        super().__init__(
            ProcessScriptSpec(
                process_name="MaaEnd.exe",
                # MaaEnd 的业务提交信息在日期命名的应用日志中，Tasker 终态
                # 事件在 maafw.log 中；不同版本的 MaaEnd 会把后者写在根目录
                # 或 cpp-algo/debug 子目录中，这两个来源正好对应“任务列表”和“任务终态”。
                log_patterns=(
                    "maafw.log",
                    "maafw.bak.*.log",
                    "cpp-algo/debug/maafw.log",
                    "cpp-algo/debug/maafw.bak.*.log",
                    "????-??-??-*.log",
                ),
                completion_mode="log_marker",
                # MaaEnd 启动时会依据自身配置自动运行本轮任务；已有实例
                # 必须先安全重启，不能只接管后等待，否则不会产生本轮日志。
                restart_existing=True,
                allow_existing=False,
                # 手动停止不再单独解析；如果停止后日志持续 5 分钟没有刷新，
                # 由通用监控逻辑结束本轮任务。
                inactivity_completion_seconds=5 * 60,
            )
        )
        self._entry_names = dict(self._control_entry_names)
        self._primary_task_ids: set[int] = set()
        self._terminal_task_ids: set[int] = set()
        self._failed_task_ids: set[int] = set()
        self._completion_logged = False

    async def start(self, context: TaskContext) -> StartResult:
        """先读取 MaaEnd 自己的任务名称映射，再启动并监听脚本。"""

        loaded_count = self._load_entry_names(context.script_path)
        get_task_logger(context.task_name).debug(
            "MaaEnd 任务名称映射：从脚本目录加载 {} 条 entry 映射",
            loaded_count,
        )
        return await super().start(context)

    def _load_entry_names(self, script_path: Path | None) -> int:
        """从 MaaEnd 的任务定义和中文本地化文件读取 entry 显示名。"""

        if script_path is None:
            return 0

        root = script_path.parent
        locale_path = root / "locales" / "interface" / "zh_cn.json"
        task_directory = root / "tasks"
        try:
            locale_data = self._load_json_file(locale_path)
        except (OSError, json.JSONDecodeError):
            return 0

        loaded: dict[str, str] = {}
        try:
            task_files = sorted(task_directory.glob("*.json"))
        except OSError:
            return 0

        for task_file in task_files:
            try:
                task_data = self._load_json_file(task_file)
            except (OSError, json.JSONDecodeError):
                continue

            task_items = task_data.get("task", [])
            if isinstance(task_items, dict):
                task_items = [task_items]
            if not isinstance(task_items, list):
                continue

            for task in task_items:
                if not isinstance(task, dict):
                    continue
                entry = task.get("entry")
                label_ref = task.get("label")
                if not isinstance(entry, str) or not isinstance(label_ref, str):
                    continue
                label_key = label_ref.removeprefix("$")
                label = locale_data.get(label_key)
                if isinstance(label, str) and label.strip():
                    loaded[entry] = label.strip()

        self._entry_names.update(loaded)
        return len(loaded)

    @staticmethod
    def _load_json_file(path: Path) -> object:
        """读取 MaaEnd 的 JSONC 配置，兼容任务文件中的行注释。"""

        text = path.read_text(encoding="utf-8-sig")
        return json.loads(MaaEndAdapter._strip_json_comments(text))

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        """移除字符串外的 ``//`` 注释，不破坏 URL 和普通文本。"""

        result: list[str] = []
        in_string = False
        escaped = False
        index = 0
        while index < len(text):
            char = text[index]
            if in_string:
                result.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue

            if char == '"':
                in_string = True
                result.append(char)
                index += 1
                continue

            if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue

            result.append(char)
            index += 1
        return "".join(result)

    def observe_logs(self, records: list[tuple[Path, str]]) -> LogObservation:
        """把 MaaEnd 事件日志转换为可读的任务进度。

        MaaEnd 会把前段业务任务和两个收尾特殊任务一起提交。这里仅记录
        前段任务 ID 的终态；前段任务失败仍按单个业务错误记录，不会让整轮
        MaaEnd 失败。手动停止日志不再单独判定任务失败，停止后由日志静默
        规则统一处理。
        """

        activity = False
        completion_started = False
        completion_seen = False
        messages: list[TaskLogLine] = []

        ordered_records = self._sort_records(records)

        # 先扫描提交信息，再解析 Tasker 事件。
        submitted_ids_in_batch: set[int] = set()
        for _, line in ordered_records:
            submitted_ids = self._extract_primary_task_ids(line)
            if submitted_ids:
                submitted_ids_in_batch.update(submitted_ids)

        if submitted_ids_in_batch:
            self._primary_task_ids.update(submitted_ids_in_batch)
            activity = True
            messages.append(
                TaskLogLine(
                    "primary-submitted:" + ",".join(
                        str(task_id) for task_id in sorted(submitted_ids_in_batch)
                    ),
                    f"前段业务任务已提交：{len(submitted_ids_in_batch)} 个",
                    reportable=False,
                )
            )

        for _, line in ordered_records:
            event = self._task_event_pattern.search(line)
            if event:
                action, payload = event.groups()
                try:
                    details = json.loads(payload)
                except json.JSONDecodeError:
                    details = {}
                entry = str(details.get("entry", "未知任务"))
                label = self._entry_names.get(entry, entry)
                event_key = f"{action}:{entry}:{details.get('uuid', line)}"
                task_id = self._parse_task_id(details.get("task_id"))
                is_primary = task_id in self._primary_task_ids
                is_control = entry in self._control_entry_names or label == "结束进程"
                activity = True
                if action == "Starting":
                    if is_control:
                        completion_started = True
                        messages.append(
                            TaskLogLine(
                                event_key,
                                f"任务开始: {label}（等待脚本退出）",
                                reportable=False,
                            )
                        )
                    elif is_primary:
                        messages.append(
                            TaskLogLine(
                                event_key,
                                f"任务开始: {label}",
                                reportable=False,
                            )
                        )
                elif action == "Succeeded":
                    if is_control:
                        completion_seen = True
                        messages.append(
                            TaskLogLine(
                                event_key,
                                f"任务完成: {label}",
                                reportable=False,
                            )
                        )
                    elif is_primary:
                        self._terminal_task_ids.add(task_id)
                        messages.append(TaskLogLine(event_key, f"任务完成: {label}"))
                else:
                    if is_primary:
                        # MaaEnd 的单个 Tasker 任务失败不代表整轮 MaaEnd 失败。
                        # 失败本身也是该 task_id 的终态，继续等待其余业务任务。
                        self._terminal_task_ids.add(task_id)
                        self._failed_task_ids.add(task_id)
                        messages.append(TaskLogLine(event_key, f"[ERR] 任务出错: {label}"))
                continue

            if self._is_detail_line(line):
                activity = True
                messages.append(
                    TaskLogLine(line, self._clean_line(line), reportable=False)
                )

        business_completed = (
            bool(self._primary_task_ids)
            and self._primary_task_ids <= self._terminal_task_ids
        )
        completed = business_completed or completion_seen
        if business_completed and not self._completion_logged:
            failed_count = len(self._failed_task_ids & self._primary_task_ids)
            suffix = f"，包含 {failed_count} 个子任务错误" if failed_count else ""
            messages.append(
                TaskLogLine(
                    "primary-complete",
                    f"业务阶段完成：{len(self._primary_task_ids)} 个前段任务已进入终态{suffix}",
                )
            )
            self._completion_logged = True

        return LogObservation(
            activity_seen=activity,
            completion_started=completion_started,
            completion_seen=completed,
            messages=tuple(messages),
        )

    def _inactivity_outcome(
        self,
        inactivity_seconds: float,
    ) -> tuple[Literal["completed", "failed"], str]:
        """MaaEnd 静默时不能代替前段任务完成。"""

        minutes = inactivity_seconds / 60
        return (
            "failed",
            f"失败依据：连续 {minutes:g} 分钟无有效业务日志，前段业务任务未全部进入终态",
        )

    @classmethod
    def _extract_primary_task_ids(cls, line: str) -> set[int]:
        match = cls._primary_task_ids_pattern.search(line)
        if not match:
            return set()
        return {
            int(value)
            for value in re.findall(r"\d+", match.group(1))
        }

    @staticmethod
    def _parse_task_id(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _sort_records(
        cls,
        records: list[tuple[Path, str]],
    ) -> list[tuple[Path, str]]:
        """按外部日志中的时间排序，修正轮转文件的读取顺序。"""

        def sort_key(item: tuple[int, tuple[Path, str]]) -> tuple[bool, object, int]:
            index, (_, line) = item
            match = cls._timestamp_pattern.search(line)
            if not match:
                return (True, "", index)
            return (False, match.group(1), index)

        return [record for _, record in sorted(enumerate(records), key=sort_key)]

    @staticmethod
    def _is_detail_line(line: str) -> bool:
        """判断是否为用户关心的业务过程日志。"""

        cleaned = MaaEndAdapter._clean_line(line)
        if re.fullmatch(r"\|\s+(?:enter|leave)(?:,\s*\d+ms)?", cleaned):
            return False

        return any(
            keyword in cleaned
            for keyword in (
                "检查物资",
                "不匹配",
                "价格变动较大",
                "任务开始:",
                "任务完成:",
            )
        ) or bool(re.search(r"\d+\s*>=\s*\d+", cleaned))

    @staticmethod
    def _clean_line(line: str) -> str:
        """去除底层日志前缀，保留业务文本。"""

        parts = line.rsplit("] ", 1)
        return parts[-1].strip()
