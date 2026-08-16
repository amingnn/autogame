"""把森空岛签到业务接入统一任务生命周期。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from autogame.config import SkylandAccountConfig
from autogame.tasks.base import AdapterResult, StartResult, TaskContext
from autogame.tasks.skyland_sign.service import run_sign_in
from autogame.tasks.skyland_sign.token_store import TokenStore


class SkylandSignAdapter:
    """在项目进程内执行森空岛签到。"""

    description = "执行内置森空岛签到并根据返回结果完成"
    requires_script = False

    def __init__(
        self,
        token_path: Path,
        account: SkylandAccountConfig | None = None,
    ) -> None:
        self._token_store = TokenStore(token_path)
        self._account = account

    async def start(self, context: TaskContext) -> StartResult:
        """在线程池执行签到并转换为统一结果。"""

        result = await asyncio.to_thread(
            run_sign_in,
            self._token_store,
            self._account,
        )
        report_lines = tuple(result.messages)
        if result.success:
            return StartResult(
                None,
                AdapterResult(
                    "completed",
                    "森空岛签到完成",
                    report_lines=report_lines,
                ),
            )
        return StartResult(
            None,
            AdapterResult(
                "failed",
                result.messages[-1] if result.messages else "签到失败",
                report_lines=report_lines,
            ),
        )

    async def poll(self, context: TaskContext, handle: object) -> AdapterResult:
        """内置任务在启动阶段已经完成。"""

        return AdapterResult("completed", "森空岛签到完成")

    async def stop(self, context: TaskContext, handle: object) -> None:
        """内置签到没有外部进程需要停止。"""

        return None
