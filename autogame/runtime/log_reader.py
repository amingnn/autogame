"""提供按文件偏移读取外部程序日志的能力。"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable


class IncrementalLogReader:
    """只读取日志文件自上次检查以来新增的内容。"""

    def __init__(self, patterns: Iterable[Path]) -> None:
        self.patterns = tuple(patterns)
        self._offsets: dict[Path, int] = {}

    def prime(self) -> None:
        """记录当前文件末尾，避免把历史日志误判为本次任务结果。"""

        for path in self._files():
            try:
                self._offsets[path] = path.stat().st_size
            except OSError:
                continue

    def read_lines(self) -> list[tuple[Path, str]]:
        """读取所有日志文件新增的文本行。"""

        result: list[tuple[Path, str]] = []
        for path in self._files():
            try:
                size = path.stat().st_size
                offset = self._offsets.get(path, 0)
                if size < offset:
                    offset = 0
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(offset)
                    text = stream.read()
                    self._offsets[path] = stream.tell()
            except OSError:
                continue
            result.extend((path, line) for line in text.splitlines() if line.strip())
        return result

    def _files(self) -> list[Path]:
        """展开日志路径并去除重复文件。"""

        files: dict[Path, None] = {}
        for pattern in self.patterns:
            if pattern.exists() and pattern.is_file():
                files[pattern] = None
                continue
            for path in pattern.parent.glob(pattern.name):
                if path.is_file():
                    files[path] = None
        return sorted(files, key=lambda path: path.stat().st_mtime_ns if path.exists() else 0)
