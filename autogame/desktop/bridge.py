"""向 pywebview 页面暴露受控的任务和配置操作。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autogame.desktop.app import DesktopBackend


class DesktopBridge:
    """把页面调用转换为后台 TaskManager 操作。"""

    def __init__(self, backend: "DesktopBackend") -> None:
        self._backend = backend

    def get_status(self) -> dict[str, object]:
        """返回当前任务和系统状态。"""

        return self._execute(self._backend.manager.get_status_snapshot)

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
            return {"ok": True, "data": self._read_recent_logs(log_dir, limit)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _execute(self, function: Any, *args: object) -> dict[str, object]:
        """统一执行同步操作并转换错误。"""

        try:
            return {"ok": True, "data": self._backend.call(function, *args)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _read_recent_logs(log_dir: Path, limit: int) -> list[str]:
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
        return files[0].read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()[-max(1, min(int(limit), 1000)) :]
