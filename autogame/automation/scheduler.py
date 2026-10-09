"""调用项目计划任务脚本读取或更新每日运行时间。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SCHEDULE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "register-autogame-task.ps1"


def get_scheduled_times() -> list[str]:
    """读取 AutoGame 计划任务中的每日触发时间。"""

    result = _run_script("-List")
    parsed = json.loads(result)
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise RuntimeError("计划任务脚本返回了无效时间列表")
    return sorted(set(parsed))


def set_scheduled_times(times: list[str]) -> list[str]:
    """把多个每日触发时间交给现有 PowerShell 模板注册。"""

    normalized = sorted(set(value.strip() for value in times if value.strip()))
    if not normalized:
        raise ValueError("至少添加一个每日运行时间")
    invalid = [value for value in normalized if not TIME_PATTERN.fullmatch(value)]
    if invalid:
        raise ValueError(f"无效的运行时间：{', '.join(invalid)}")
    _run_script("-TimesCsv", ",".join(normalized))
    return get_scheduled_times()


def _run_script(*arguments: str) -> str:
    """在 Windows PowerShell 中调用项目自带计划任务模板。"""

    powershell = "powershell.exe"
    if sys.platform != "win32":
        raise RuntimeError("计划任务设置仅支持 Windows")
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCHEDULE_SCRIPT),
        *arguments,
    ]
    if "-TimesCsv" in arguments:
        command.extend(["-UvPath", str(_find_uv_executable())])
    result = subprocess.run(
        command,
        cwd=SCHEDULE_SCRIPT.parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"计划任务脚本退出码：{result.returncode}")
    return result.stdout.strip()


def _find_uv_executable() -> Path:
    """从 MY_ENV 或 PATH 定位定时任务需要的 uv。"""

    root = os.environ.get("MY_ENV", "")
    if not root and sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                root, _ = winreg.QueryValueEx(key, "MY_ENV")
        except FileNotFoundError:
            root = ""
    candidate = Path(root) / "uv" / "uv.exe" if root else None
    if candidate is not None and candidate.is_file():
        return candidate
    located = shutil.which("uv")
    if located:
        return Path(located)
    raise RuntimeError("找不到 uv.exe，请检查 MY_ENV 或 PATH")
