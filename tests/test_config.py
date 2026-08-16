"""测试配置保存、路径和日志保留。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from autogame.config import AppPaths, Config, SystemConfig
from autogame.config_store import ConfigConflictError, ConfigStore
from autogame.logger import _cleanup_old_logs


class ConfigTests(unittest.TestCase):
    """验证正式配置编辑行为。"""

    def test_paths_keep_runtime_data_outside_source_package(self) -> None:
        paths = AppPaths(root=Path("D:/demo"))
        self.assertEqual(paths.config_file, Path("D:/demo/config.yaml"))
        self.assertEqual(paths.state_file, Path("D:/demo/data/state.json"))
        self.assertEqual(
            paths.skyland_token_file,
            Path("D:/demo/data/skyland_sign/token.txt"),
        )

    def test_explicit_config_path_is_preserved(self) -> None:
        config = Config.load(Path("D:/demo/custom.yaml"))
        self.assertEqual(config.cfg_path, Path("D:/demo/custom.yaml"))

        data_config = Config.load(Path("D:/demo/data/config.yaml"))
        self.assertEqual(data_config.db_path, Path("D:/demo/data/state.json"))

    def test_save_preserves_comments_backup_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "system:\n"
                "  log_level: INFO\n"
                "tasks:\n"
                "  demo:\n"
                "    enabled: true # 任务注释\n"
                "    interval_hours: 1\n",
                encoding="utf-8",
            )
            store = ConfigStore(path)
            revision = store.revision()
            store.update_task(
                "demo",
                {"interval_hours": 2, "script_path": "C:/demo.exe"},
                expected_revision=revision,
            )

            text = path.read_text(encoding="utf-8")
            self.assertIn("任务注释", text)
            self.assertIn("interval_hours: 2", text)
            self.assertTrue(path.with_name("config.yaml.bak").exists())
            with self.assertRaises(ConfigConflictError):
                store.update_task(
                    "demo",
                    {"interval_hours": 3},
                    expected_revision=revision,
                )

    def test_removed_webhook_port_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "system:\n  webhook_port: 8000\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config.load(path)

    def test_completion_action_uses_minute_timeout(self) -> None:
        config = SystemConfig(
            automation_timeout_minutes=30,
            completion_action="hibernate",
            completion_action_delay_seconds=60,
        )

        self.assertEqual(config.automation_timeout_minutes, 30)
        self.assertTrue(config.server_chan_enabled)
        self.assertFalse(hasattr(config, "shutdown_on_complete"))

    def test_server_chan_enabled_can_be_updated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "system:\n  server_chan_enabled: true\n",
                encoding="utf-8",
            )

            ConfigStore(path).update_system({"server_chan_enabled": False})

            self.assertFalse(Config.load(path).system.server_chan_enabled)

    def test_cleanup_keeps_recent_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            old_log = log_dir / "old.log"
            recent_log = log_dir / "recent.log"
            old_log.write_text("old", encoding="utf-8")
            recent_log.write_text("recent", encoding="utf-8")
            old_time = time.time() - 8 * 24 * 60 * 60
            os.utime(old_log, (old_time, old_time))

            _cleanup_old_logs(log_dir)

            self.assertFalse(old_log.exists())
            self.assertTrue(recent_log.exists())
