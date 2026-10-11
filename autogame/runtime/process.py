"""提供 Windows 进程启动、验证和安全停止能力。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


_OLD_PROCESS_EXIT_GRACE_SECONDS = 2.0
_PROCESS_RECHECK_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class ProcessHandle:
    """记录一次进程启动或复用结果。"""

    pid: int
    process_name: str
    created_at: float | None
    owned: bool
    restarted: bool = False
    executable_path: Path | None = None
    launcher: subprocess.Popen | None = None
    terminated_pids: tuple[int, ...] = ()


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
    """确认脚本仍在运行，并兼容启动器替换实际进程的情况。"""

    return bool(_processes_for_handle(handle))


def start_process(
    path: Path,
    process_name: str,
    timeout_seconds: float = 15.0,
    allow_existing: bool = True,
    restart_existing: bool = False,
    launch_via_shell: bool = False,
) -> ProcessHandle:
    """启动应用并在限定时间内确认目标进程存在。"""

    if not path.exists():
        raise FileNotFoundError(f"启动路径不存在：{path}")

    if restart_existing and path.suffix.casefold() == ".lnk":
        raise ValueError("关闭旧脚本实例必须配置实际 exe 路径，不能使用快捷方式")
    tracked_executable_path = (
        path.resolve() if path.suffix.casefold() != ".lnk" else None
    )
    executable_path = tracked_executable_path if restart_existing else None
    existing = find_processes(process_name, executable_path)
    restarted = bool(existing and restart_existing)
    terminated_pids: tuple[int, ...] = ()
    if restarted:
        terminated_pids = tuple(process.pid for process in existing)
        for process in existing:
            _terminate_process_tree(process)
        # 某些启动器收到终止请求后还需要一点时间才会从进程表消失。
        # 终止函数返回后短暂重试，避免把正在退出的旧实例误报为关闭失败。
        remaining = _wait_for_process_exit(
            process_name,
            executable_path,
            timeout=_OLD_PROCESS_EXIT_GRACE_SECONDS,
        )
        if remaining:
            pids = ", ".join(str(process.pid) for process in remaining)
            raise RuntimeError(
                f"无法关闭旧脚本实例：{process_name}；"
                f"项目已发送终止请求（原 PID：{', '.join(map(str, terminated_pids))}），"
                f"复查仍存在 PID：{pids}"
            )
        existing = []

    if existing:
        if not allow_existing:
            raise RuntimeError(f"目标脚本已经在运行：{process_name}")
        process = max(existing, key=_safe_create_time)
        handle = _to_handle(
            process,
            process_name,
            owned=False,
            executable_path=tracked_executable_path,
            terminated_pids=terminated_pids,
        )
        return handle

    launch_started_at = time.time()
    launcher: subprocess.Popen | None = None
    if path.suffix.casefold() == ".lnk" or launch_via_shell:
        os.startfile(str(path))
    else:
        launcher = subprocess.Popen(  # noqa: S603
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
                    executable_path=tracked_executable_path,
                    launcher=launcher,
                    terminated_pids=terminated_pids,
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
    launch_via_shell: bool = False,
) -> ProcessHandle:
    """在线程池中启动进程，避免阻塞异步服务。"""

    return await asyncio.to_thread(
        start_process,
        path,
        process_name,
        timeout_seconds,
        allow_existing,
        restart_existing,
        launch_via_shell,
    )


def stop_process_tree(handle: ProcessHandle) -> None:
    """只停止本次启动且仍归项目管理的进程树。"""

    if not handle.owned:
        return

    for process in _processes_for_handle(handle):
        _terminate_process_tree(process)


def _wait_for_process_exit(
    process_name: str,
    executable_path: Path | None,
    timeout: float,
) -> list[psutil.Process]:
    """等待目标进程从系统进程表消失，处理终止后的短暂可见窗口。"""

    deadline = time.monotonic() + timeout
    while True:
        remaining = find_processes(process_name, executable_path)
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(_PROCESS_RECHECK_INTERVAL_SECONDS)


def _to_handle(
    process: psutil.Process,
    process_name: str,
    owned: bool,
    restarted: bool = False,
    executable_path: Path | None = None,
    launcher: subprocess.Popen | None = None,
    terminated_pids: tuple[int, ...] = (),
) -> ProcessHandle:
    """把 psutil 进程转换为稳定的项目进程句柄。"""

    return ProcessHandle(
        pid=process.pid,
        process_name=process_name,
        created_at=_safe_create_time(process),
        owned=owned,
        restarted=restarted,
        executable_path=executable_path,
        launcher=launcher,
        terminated_pids=terminated_pids,
    )


def _processes_for_handle(handle: ProcessHandle) -> list[psutil.Process]:
    """返回句柄对应的进程；MaaEnd 启动器换 PID 时按同路径接管。"""

    try:
        process = psutil.Process(handle.pid)
        if _matches_handle_process(process, handle):
            return [process]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    if not handle.owned or handle.executable_path is None:
        return []
    return find_processes(handle.process_name, handle.executable_path)


def _matches_handle_process(
    process: psutil.Process,
    handle: ProcessHandle,
) -> bool:
    """校验 PID、进程名、创建时间和可选的实际 exe 路径。"""

    try:
        if process.name().casefold() != Path(handle.process_name).name.casefold():
            return False
        if handle.executable_path is not None:
            actual_path = process.exe()
            if _normalized_path(Path(actual_path)) != _normalized_path(
                handle.executable_path
            ):
                return False
        if handle.created_at is not None and abs(
            process.create_time() - handle.created_at
        ) >= 0.01:
            return False
        return process.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _safe_create_time(process: psutil.Process) -> float:
    """读取进程创建时间，失败时返回零。"""

    try:
        return process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def describe_process_handle(handle: ProcessHandle) -> str:
    """返回用于诊断启动和退出原因的进程快照。"""

    details = [
        f"目标 PID={handle.pid}",
        f"owned={handle.owned}",
        f"restarted={handle.restarted}",
    ]
    if handle.terminated_pids:
        details.append(
            "项目终止旧 PID=" + ",".join(str(pid) for pid in handle.terminated_pids)
        )
    if handle.launcher is not None:
        try:
            details.append(
                f"Popen PID={handle.launcher.pid} 返回码={handle.launcher.poll()}"
            )
        except OSError as exc:
            details.append(f"Popen 状态读取失败={exc}")

    try:
        process = psutil.Process(handle.pid)
        details.extend(
            (
                f"状态={process.status()}",
                f"父 PID={process.ppid()}",
                f"exe={process.exe()}",
            )
        )
        try:
            children = process.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            children = []
        if children:
            details.append("子进程=" + ",".join(str(child.pid) for child in children))
    except psutil.NoSuchProcess:
        details.append("目标 PID 已不存在")
    except (psutil.AccessDenied, psutil.ZombieProcess) as exc:
        details.append(f"目标进程状态不可读取={exc}")
    return "；".join(details)


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
    _, alive = _wait_procs_safely(targets, timeout=10)
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if alive:
        _wait_procs_safely(alive, timeout=5)


def _wait_procs_safely(
    processes: list[psutil.Process],
    timeout: float,
) -> tuple[list[psutil.Process], list[psutil.Process]]:
    """等待进程退出，并把等待期间消失的 PID 视为已退出。"""

    try:
        return psutil.wait_procs(processes, timeout=timeout)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        # Windows 上进程可能在 terminate() 与 wait_procs() 之间自行退出。
        # 重新检查时，已消失的进程不应让本次启动失败；权限不足则保留，
        # 让调用方后续重新扫描并决定是否确实无法关闭。
        alive: list[psutil.Process] = []
        for process in processes:
            try:
                if process.is_running():
                    alive.append(process)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                alive.append(process)
        return [], alive


def _normalized_path(path: Path) -> str:
    """生成用于 Windows 可执行文件路径比较的规范字符串。"""

    return os.path.normcase(os.path.abspath(str(path)))
