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


def _matches_process(process: psutil.Process, process_name: str) -> bool:
    """按进程名匹配目标进程。"""

    try:
        actual_name = process.info.get("name") or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return actual_name.casefold() == Path(process_name).name.casefold()


def _find_processes(process_name: str) -> list[psutil.Process]:
    """返回当前所有匹配进程。"""

    result: list[psutil.Process] = []
    for process in psutil.process_iter(["name"]):
        if _matches_process(process, process_name):
            result.append(process)
    return result


def _get_process_create_time(process: psutil.Process) -> float | None:
    """读取进程创建时间，用于排除启动前就存在的同名进程。"""

    try:
        return process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _stop_existing_processes(processes: list[psutil.Process], process_name: str) -> None:
    """停止旧进程，确保本次启动能够被识别为真实启动。"""

    if not processes:
        return

    targets: dict[int, psutil.Process] = {}
    for process in processes:
        try:
            targets[process.pid] = process
            for child in process.children(recursive=True):
                targets[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    mlog.warning("发现已运行的目标进程，准备重启：{}（{} 个）", process_name, len(targets))
    for process in targets.values():
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        remaining = _find_processes(process_name)
        if not remaining:
            return
        time.sleep(0.1)

    remaining = _find_processes(process_name)
    for process in remaining:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if _find_processes(process_name):
        raise RuntimeError(f"无法停止旧的目标进程：{process_name}")


def _start_and_verify_sync(launcher: LauncherConfig) -> None:
    """启动应用并在限定时间内确认目标进程真实存在。"""

    path = Path(launcher.path)
    if not path.exists():
        raise FileNotFoundError(f"应用启动路径不存在：{path}")

    before = {
        process.pid: _get_process_create_time(process)
        for process in _find_processes(launcher.process_name)
    }
    if before:
        if not launcher.restart_existing:
            mlog.info("目标进程已存在，按配置复用：{}", launcher.process_name)
            return
        _stop_existing_processes(
            [
                process
                for process in _find_processes(launcher.process_name)
                if process.pid in before
            ],
            launcher.process_name,
        )

    launch_started_at = time.time()
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
        processes = _find_processes(launcher.process_name)
        if any(
            process.pid not in before
            and (
                (created_at := _get_process_create_time(process)) is not None
                and created_at >= launch_started_at
            )
            for process in processes
        ):
            mlog.info("应用启动验证成功：{}", launcher.process_name)
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"应用启动验证超时：{launcher.process_name}"
        f"（等待 {launcher.startup_timeout_seconds:.1f} 秒）"
    )


async def start_and_verify(launcher: LauncherConfig) -> None:
    """在线程池中启动应用，避免阻塞异步 API。"""

    if launcher.type == "none":
        return
    await asyncio.to_thread(_start_and_verify_sync, launcher)
