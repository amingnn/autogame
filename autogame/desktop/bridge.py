"""向 pywebview 页面暴露受控的任务和配置操作。"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autogame.desktop.app import DesktopBackend


class DesktopBridge:
    """把页面调用转换为后台 TaskManager 操作。"""

    def __init__(self, backend: "DesktopBackend") -> None:
        self._backend = backend
        self._window: Any = None
        self._maximized = False
        self._log_session_started_at = datetime.now()

    def _bind_window(self, window: Any) -> None:
        """绑定 pywebview 窗口，供自绘标题栏控制窗口状态。"""

        self._window = window

    def minimize_window(self) -> dict[str, object]:
        """最小化桌面窗口。"""

        return self._window_action("minimize")

    def toggle_maximize_window(self) -> dict[str, object]:
        """切换桌面窗口的最大化和还原状态。"""

        if self._window is None:
            return {"ok": False, "error": "桌面窗口尚未初始化"}
        try:
            if self._maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._maximized = not self._maximized
            return {"ok": True, "maximized": self._maximized}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_task(self, task_name: str) -> dict[str, object]:
        """停止指定任务及其进程树。"""

        try:
            stopped = self._backend.submit(
                self._backend.manager.stop_task(task_name, "用户点击暂停")
            )
            return {
                "ok": True,
                "stopped": stopped,
                "message": "任务已暂停" if stopped else "任务当前未运行",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close_window(self) -> dict[str, object]:
        """关闭桌面窗口。"""

        return self._window_action("destroy")

    def get_status(self) -> dict[str, object]:
        """返回当前任务和系统状态。"""

        return self._execute(self._backend.manager.get_status_snapshot)

    def get_server_chan_key(self) -> dict[str, object]:
        """仅在用户主动查看时返回已保存的 Server 酱 Key。"""

        try:
            return {
                "ok": True,
                "data": {"key": self._backend.manager.config.system.server_chan_key},
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def run_task(self, task_name: str, force: bool = False) -> dict[str, object]:
        """从桌面手动启动指定任务。"""

        try:
            accepted = self._backend.submit(
                self._backend.manager.run_task(task_name, force=bool(force))
            )
            return {
                "ok": True,
                "accepted": accepted,
                "message": "任务已启动" if accepted else "任务未启动，请检查当前状态",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_all_tasks(self) -> dict[str, object]:
        """停止桌面模式当前启动的全部任务及其进程树。"""

        try:
            stopped = self._backend.submit(
                self._backend.manager.stop_active_tasks("用户点击全部停止")
            )
            return {
                "ok": True,
                "stopped": stopped,
                "message": f"已停止 {stopped} 个运行中的任务",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def update_task_config(
        self,
        task_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """保存页面提交的任务配置。"""

        data = dict(payload or {})
        revision = data.pop("config_revision", None)
        return self._execute(
            self._backend.manager.update_task_config,
            task_name,
            data,
            revision,
        )

    def update_system_config(self, payload: dict[str, object]) -> dict[str, object]:
        """保存全局配置，并按页面约定处理 SendKey。"""

        data = dict(payload or {})
        revision = data.pop("config_revision", None)
        clear_key = bool(data.pop("clear_server_chan_key", False))
        if clear_key:
            data["server_chan_key"] = ""
        elif not data.get("server_chan_key"):
            data.pop("server_chan_key", None)
        return self._execute(
            self._backend.manager.update_system_config,
            data,
            revision,
        )

    def reload_config(self) -> dict[str, object]:
        """重新加载正式配置文件。"""

        return self._execute(self._backend.manager.reload_config)

    def get_recent_logs(self, limit: int = 100) -> dict[str, object]:
        """读取最近主日志的末尾内容。"""

        try:
            log_dir = self._backend.manager.config.log_dir
            return {
                "ok": True,
                "data": self._read_recent_logs(
                    log_dir,
                    limit,
                    since=self._log_session_started_at,
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_logs_folder(self) -> dict[str, object]:
        """打开当前配置的日志目录。"""

        try:
            log_dir = self._backend.manager.config.log_dir
            log_dir.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(log_dir))  # type: ignore[attr-defined]
            elif os.name == "posix":
                subprocess.Popen(["xdg-open", str(log_dir)])  # noqa: S603
            else:
                raise RuntimeError("当前系统不支持打开日志文件夹")
            return {"ok": True, "message": "日志文件夹已打开"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_window_size(self) -> dict[str, object]:
        """返回当前窗口尺寸，供无边框缩放热区使用。"""

        if self._window is None:
            return {"ok": False, "error": "桌面窗口尚未初始化"}
        try:
            return {
                "ok": True,
                "width": int(self._window.width),
                "height": int(self._window.height),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def resize_window(self, width: int, height: int, edge: str) -> dict[str, object]:
        """按页面边缘拖拽结果调整无边框窗口尺寸。"""

        if self._window is None:
            return {"ok": False, "error": "桌面窗口尚未初始化"}
        try:
            from webview.window import FixPoint

            direction = str(edge or "se").lower()
            if direction not in {"n", "e", "s", "w", "ne", "se", "sw", "nw"}:
                raise ValueError("无效的窗口缩放方向")
            min_width, min_height = self._window.min_size
            target_width = max(int(width), int(min_width))
            target_height = max(int(height), int(min_height))
            fix_point = FixPoint.SOUTH if "n" in direction else FixPoint.NORTH
            fix_point |= FixPoint.EAST if "w" in direction else FixPoint.WEST
            self._window.resize(target_width, target_height, fix_point)
            return {
                "ok": True,
                "width": target_width,
                "height": target_height,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _execute(self, function: Any, *args: object) -> dict[str, object]:
        """统一执行同步操作并转换错误。"""

        try:
            return {"ok": True, "data": self._backend.call(function, *args)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _window_action(self, action: str) -> dict[str, object]:
        """执行一个自绘标题栏所需的窗口动作。"""

        if self._window is None:
            return {"ok": False, "error": "桌面窗口尚未初始化"}
        try:
            getattr(self._window, action)()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _read_recent_logs(
        log_dir: Path,
        limit: int,
        since: datetime | None = None,
    ) -> list[str]:
        """读取最近一个主日志文件。"""

        files = sorted(
            (
                path
                for path in log_dir.glob("*.log")
                if not path.name.startswith("notify-")
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return []
        lines = files[0].read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
        if since is not None:
            current_lines: list[str] = []
            for line in lines:
                try:
                    timestamp = datetime.fromisoformat(line.split(" | ", 1)[0])
                except (ValueError, IndexError):
                    continue
                if timestamp >= since:
                    current_lines.append(line)
            lines = current_lines
        return lines[-max(1, min(int(limit), 1000)) :]
