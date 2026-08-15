"""提供 Windows 进程启动、验证和安全停止能力。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

@dataclass(frozen=True)
class ProcessHandle:
    """记录一次进程启动或复用结果。"""

    pid: int
    process_name: str
    created_at: float | None
    owned: bool
    restarted: bool = False


def find_processes(
    process_name: str,
    executable_path: Path | None = None,
) -> list[psutil.Process]:
    """按进程名查找进程；提供路径时只返回完全匹配的实例。"""

    expected = Path(process_name).name.casefold()
    expected_path = _normalized_path(executable_path) if executable_path else None
    result: list[psutil.Process] = []
    for process in psutil.process_iter(["name", "exe"]):
        try:
            info = process.info
            actual = (info.get("name") or "").casefold()
            actual_path = info.get("exe") if expected_path is not None else None
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if actual != expected:
            continue
        if expected_path is not None:
            if not actual_path or _normalized_path(Path(actual_path)) != expected_path:
                continue
        result.append(process)
    return result


def process_is_running(handle: ProcessHandle) -> bool:
    """确认句柄对应的仍是同一个进程，而不是复用后的 PID。"""

    try:
        process = psutil.Process(handle.pid)
        if process.name().casefold() != Path(handle.process_name).name.casefold():
            return False
        if handle.created_at is None:
            return process.is_running()
        return abs(process.create_time() - handle.created_at) < 0.01 and process.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def start_process(
    path: Path,
    process_name: str,
    timeout_seconds: float = 15.0,
    allow_existing: bool = True,
    restart_existing: bool = False,
) -> ProcessHandle:
    """启动应用并在限定时间内确认目标进程存在。"""

    if not path.exists():
        raise FileNotFoundError(f"启动路径不存在：{path}")

    if restart_existing and path.suffix.casefold() == ".lnk":
        raise ValueError("关闭旧脚本实例必须配置实际 exe 路径，不能使用快捷方式")
    executable_path = path.resolve() if restart_existing else None
    existing = find_processes(process_name, executable_path)
    restarted = bool(existing and restart_existing)
    if restarted:
        for process in existing:
            _terminate_process_tree(process)
        if find_processes(process_name, executable_path):
            raise RuntimeError(f"无法关闭旧脚本实例：{process_name}")
        existing = []

    if existing:
        if not allow_existing:
            raise RuntimeError(f"目标脚本已经在运行：{process_name}")
        process = max(existing, key=_safe_create_time)
        handle = _to_handle(process, process_name, owned=False)
        return handle

    launch_started_at = time.time()
    if path.suffix.casefold() == ".lnk":
        os.startfile(str(path))
    else:
        subprocess.Popen(  # noqa: S603
            [str(path)],
            cwd=str(path.parent),
            close_fds=True,
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for process in find_processes(process_name, executable_path):
            created_at = _safe_create_time(process)
            if created_at is None or created_at >= launch_started_at - 0.5:
                handle = _to_handle(
                    process,
                    process_name,
                    owned=True,
                    restarted=restarted,
                )
                return handle
        time.sleep(0.25)

    raise RuntimeError(
        f"进程启动验证超时：{process_name}（等待 {timeout_seconds:.1f} 秒）"
    )


async def start_process_async(
    path: Path,
    process_name: str,
    timeout_seconds: float = 15.0,
    allow_existing: bool = True,
    restart_existing: bool = False,
) -> ProcessHandle:
    """在线程池中启动进程，避免阻塞异步服务。"""

    return await asyncio.to_thread(
        start_process,
        path,
        process_name,
        timeout_seconds,
        allow_existing,
        restart_existing,
    )


def stop_process_tree(handle: ProcessHandle) -> None:
    """只停止本次启动且仍归项目管理的进程树。"""

    if not handle.owned or not process_is_running(handle):
        return
    try:
        process = psutil.Process(handle.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    _terminate_process_tree(process)


def _to_handle(
    process: psutil.Process,
    process_name: str,
    owned: bool,
    restarted: bool = False,
) -> ProcessHandle:
    """把 psutil 进程转换为稳定的项目进程句柄。"""

    return ProcessHandle(
        pid=process.pid,
        process_name=process_name,
        created_at=_safe_create_time(process),
        owned=owned,
        restarted=restarted,
    )


def _safe_create_time(process: psutil.Process) -> float:
    """读取进程创建时间，失败时返回零。"""

    try:
        return process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _terminate_process_tree(process: psutil.Process) -> None:
    """先正常终止进程树，超时后再强制结束残留进程。"""

    try:
        children = process.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    targets = [*children, process]
    for target in targets:
        try:
            target.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(targets, timeout=10)
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if alive:
        psutil.wait_procs(alive, timeout=5)


def _normalized_path(path: Path) -> str:
    """生成用于 Windows 可执行文件路径比较的规范字符串。"""

    return os.path.normcase(os.path.abspath(str(path)))
