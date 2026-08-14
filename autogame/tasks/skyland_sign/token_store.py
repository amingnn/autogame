"""读取和保存森空岛 Token。"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path


class TokenStore:
    """管理环境变量和本地文件中的森空岛 Token。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[str]:
        """优先读取环境变量，否则读取本地 Token 文件。"""

        environment = os.environ.get("TOKEN", "")
        if environment:
            return self._unique(
                self.parse(value) for value in environment.split(",")
            )
        if not self.path.exists():
            return []
        return self._unique(
            self.parse(line) for line in self.path.read_text(encoding="utf-8").splitlines()
        )

    def save(self, tokens: list[str]) -> None:
        """覆盖保存去重后的 Token。"""

        values = self._unique(tokens)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(values) + "\n", encoding="utf-8")

    def is_configured(self) -> bool:
        """返回当前是否存在可用 Token。"""

        return bool(self.load())

    @staticmethod
    def parse(value: str) -> str:
        """兼容森空岛网页账号信息 JSON 和纯 Token。"""

        value = value.strip()
        if not value:
            return ""
        try:
            payload = json.loads(value)
            return str(payload["data"]["content"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError):
            return value

    @staticmethod
    def _unique(values: Iterable[str]) -> list[str]:
        """保持顺序并去除空值和重复值。"""

        result: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in result:
                result.append(text)
        return result
