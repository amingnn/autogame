"""封装自动化完成后的 Windows 电源操作。"""

from __future__ import annotations

import asyncio
import platform
import subprocess
from typing import Literal

from autogame.logger import mlog


PowerAction = Literal["shutdown", "sleep", "hibernate", "none"]


class PowerController:
    """按配置延迟执行关机、睡眠或休眠。"""

    async def execute(self, action: PowerAction, delay_seconds: int) -> None:
        """执行电源动作；不支持的平台只记录警告。"""

        if action == "none":
            mlog.info("任务完成，不执行系统电源操作")
            return
        if platform.system() != "Windows":
            mlog.warning("当前系统不支持配置的电源操作：{}", action)
            return

        mlog.info("系统将在 {} 秒后执行 {}", delay_seconds, action)
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", str(delay_seconds)])
            return

        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        if action == "hibernate":
            subprocess.Popen(["shutdown", "/h"])
        elif action == "sleep":
            subprocess.Popen(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
            )
