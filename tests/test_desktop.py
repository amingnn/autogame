"""测试桌面 Bridge 和入口解耦。"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from autogame.config import AppPaths, Config, SystemConfig, TaskConfig
from autogame.desktop.app import DesktopBackend
from autogame.desktop.bridge import DesktopBridge
from autogame.task_manager import TaskManager


class DirectBackend:
    """不启动线程的 Bridge 测试后端。"""

    def __init__(self, manager: TaskManager) -> None:
        self.manager = manager

    def call(self, function: object, *args: object, timeout: float = 30) -> object:
        return function(*args)  # type: ignore[operator]

    def submit(self, coroutine: object, timeout: float = 300) -> object:
        return asyncio.run(coroutine)  # type: ignore[arg-type]


class DesktopTests(unittest.TestCase):
    """验证无 HTTP 桌面接口。"""

    def make_config_file(self, root: Path) -> Config:
        path = root / "config.yaml"
        path.write_text(
            "system:\n"
            "  completion_action: none\n"
            "  server_chan_key: secret-value\n"
            "tasks:\n"
            "  skyland_sign:\n"
            "    enabled: true\n"
            "    interval_hours: 1\n",
            encoding="utf-8",
        )
        return Config.load(path)

    def test_bridge_updates_config_without_exposing_send_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(self.make_config_file(Path(directory)))
            bridge = DesktopBridge(DirectBackend(manager))  # type: ignore[arg-type]
            status = bridge.get_status()
            self.assertTrue(status["ok"])
            self.assertNotIn("secret-value", str(status))
            revealed = bridge.get_server_chan_key()
            self.assertEqual(revealed["data"]["key"], "secret-value")  # type: ignore[index]
            revision = status["data"]["config_revision"]  # type: ignore[index]

            result = bridge.update_task_config(
                "skyland_sign",
                {"interval_hours": 2, "config_revision": revision},
            )
            self.assertTrue(result["ok"])

    def test_desktop_backend_does_not_start_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                paths=AppPaths(root=Path(directory)),
                system=SystemConfig(completion_action="none"),
                tasks={"demo": TaskConfig(enabled=True)},
            )
            backend = DesktopBackend(config)
            with patch.object(backend.manager, "run_task") as run_task:
                backend.start()
                backend.stop()
                run_task.assert_not_called()

    def test_automation_entry_does_not_import_desktop(self) -> None:
        config = Config(
            paths=AppPaths(root=Path.cwd()),
            system=SystemConfig(completion_action="none"),
            tasks={},
        )
        sys.modules.pop("autogame.desktop", None)

        def close_coroutine(coroutine: object) -> None:
            coroutine.close()  # type: ignore[attr-defined]

        with (
            patch.object(
                main,
                "_parse_args",
                return_value=argparse.Namespace(automation=True, force=False),
            ),
            patch.object(main.Config, "load", return_value=config),
            patch("asyncio.run", side_effect=close_coroutine),
        ):
            main.main()
        self.assertNotIn("autogame.desktop", sys.modules)

    def test_short_automation_argument(self) -> None:
        with patch.object(sys, "argv", ["main.py", "-a"]):
            arguments = main._parse_args()

        self.assertTrue(arguments.automation)

    def test_force_automation_argument(self) -> None:
        with patch.object(sys, "argv", ["main.py", "-a", "-f"]):
            arguments = main._parse_args()

        self.assertTrue(arguments.automation)
        self.assertTrue(arguments.force)
