"""提供 Windows 应用启动和真实进程验证能力。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import psutil

from core.common import LauncherConfig
from core.logger import mlog


def _匹配进程(process: psutil.Process, process_name: str) -> bool:
    """按进程名匹配目标进程。"""

    try:
        actual_name = process.info.get("name") or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return actual_name.casefold() == Path(process_name).name.casefold()


def _查找进程(process_name: str) -> list[psutil.Process]:
    """返回当前所有匹配进程。"""

    result: list[psutil.Process] = []
    for process in psutil.process_iter(["name"]):
        if _匹配进程(process, process_name):
            result.append(process)
    return result


def _启动并验证同步(launcher: LauncherConfig) -> None:
    """启动应用并在限定时间内确认目标进程真实存在。"""

    path = Path(launcher.path)
    if not path.exists():
        raise FileNotFoundError(f"应用启动路径不存在：{path}")

    before = {process.pid for process in _查找进程(launcher.process_name)}
    if before:
        mlog.info("目标进程已存在，跳过重复启动：{}", launcher.process_name)
        return

    if path.suffix.casefold() == ".lnk":
        os.startfile(str(path))
    else:
        subprocess.Popen(  # noqa: S603
            [str(path)],
            cwd=str(path.parent),
            close_fds=True,
        )

    deadline = time.monotonic() + launcher.startup_timeout_seconds
    while time.monotonic() < deadline:
        processes = _查找进程(launcher.process_name)
        if any(process.pid not in before for process in processes):
            mlog.info("应用启动验证成功：{}", launcher.process_name)
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"应用启动验证超时：{launcher.process_name}"
        f"（等待 {launcher.startup_timeout_seconds:.1f} 秒）"
    )


async def 启动并验证(launcher: LauncherConfig) -> None:
    """在线程池中启动应用，避免阻塞异步 API。"""

    if launcher.type == "none":
        return
    await asyncio.to_thread(_启动并验证同步, launcher)
