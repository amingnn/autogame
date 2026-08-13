"""验证新任务生命周期、配置保存和桌面管理接口。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from core.common import Config, LauncherConfig, SystemConfig, TaskConfig
from core.config_store import ConfigStore, 迁移旧版配置
from core.scheduler import Scheduler
from core.task_registry import TaskDefinition
from core.logger import _清理旧日志
from webhook import create_app


class 配置保存测试(unittest.TestCase):
    """测试 YAML 注释、备份、版本和全局配置保存。"""

    def test_保存配置会保留注释并创建备份(self) -> None:
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
            store.update_task("demo", {"interval_hours": 2})
            store.update_system({"shutdown_delay_seconds": 30})

            text = path.read_text(encoding="utf-8")
            self.assertIn("任务注释", text)
            self.assertIn("interval_hours: 2", text)
            self.assertIn("shutdown_delay_seconds: 30", text)
            self.assertTrue(path.with_name("config.yaml.bak").exists())

    def test_日志只保留最近七天(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            old_log = log_dir / "old.log"
            recent_log = log_dir / "recent.log"
            old_log.write_text("old", encoding="utf-8")
            recent_log.write_text("recent", encoding="utf-8")
            old_time = time.time() - 8 * 24 * 60 * 60
            os.utime(old_log, (old_time, old_time))

            _清理旧日志(log_dir)

            self.assertFalse(old_log.exists())
            self.assertTrue(recent_log.exists())

    def test_旧配置迁移并移除旧入口字段(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "tasks:\n"
                "  skyland_sign:\n"
                "    enabled: true\n"
                "    interval_hours: 20\n"
                "    entry: skyland_sign.skyland.start\n"
                "    start_on: entry\n"
                "    done_on: entry\n",
                encoding="utf-8",
            )
            self.assertTrue(迁移旧版配置(path))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("entry:", text)
            self.assertIn("type: none", text)
            self.assertTrue(path.with_name("config.yaml.bak").exists())


class 任务状态测试(unittest.IsolatedAsyncioTestCase):
    """测试内置任务和应用回调任务的状态转换。"""

    async def test_内置任务完成(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(
                cfg_path=root / "config.yaml",
                db_path=root / "state.json",
                log_dir=root / "logs",
                system=SystemConfig(shutdown_on_complete=False),
                tasks={"demo": TaskConfig(enabled=True)},
            )
            scheduler = Scheduler(config, auto_shutdown=False)
            definition = TaskDefinition("demo", "internal", lambda: True)
            with patch("core.scheduler.get_task_definition", return_value=definition):
                self.assertTrue(await scheduler.run_task("demo", force=True))
            state = scheduler.get_status_snapshot()["tasks"][0]["state"]  # type: ignore[index]
            self.assertEqual(state, "completed")

    async def test_应用启动后等待回调再完成(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(
                cfg_path=root / "config.yaml",
                db_path=root / "state.json",
                log_dir=root / "logs",
                system=SystemConfig(shutdown_on_complete=False),
                tasks={
                    "maa": TaskConfig(
                        enabled=True,
                        launcher=LauncherConfig(
                            type="application",
                            path="C:/MAA.exe.lnk",
                            process_name="MAA.exe",
                        ),
                    )
                },
            )
            scheduler = Scheduler(config, auto_shutdown=False)
            definition = TaskDefinition("maa", "maa_post")
            with (
                patch("core.scheduler.get_task_definition", return_value=definition),
                patch("core.scheduler.启动并验证", new=AsyncMock()),
            ):
                self.assertTrue(await scheduler.run_task("maa", force=True))
            task = scheduler.get_status_snapshot()["tasks"][0]  # type: ignore[index]
            self.assertEqual(task["state"], "running")
            self.assertTrue(task["waiting_for_callback"])
            self.assertTrue(scheduler.mark_done("maa"))
            task = scheduler.get_status_snapshot()["tasks"][0]  # type: ignore[index]
            self.assertEqual(task["state"], "completed")
            self.assertFalse(scheduler.mark_done("maa"))

    async def test_桌面调度器不会自动扫描(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scheduler = Scheduler(
                Config(
                    cfg_path=root / "config.yaml",
                    db_path=root / "state.json",
                    log_dir=root / "logs",
                    system=SystemConfig(shutdown_on_complete=False),
                    tasks={"demo": TaskConfig(enabled=True)},
                ),
                auto_shutdown=False,
                auto_schedule=False,
            )
            with patch.object(scheduler, "run_task", new=AsyncMock()) as run_task:
                await scheduler.poll_loop()
                run_task.assert_not_awaited()


class 管理接口测试(unittest.TestCase):
    """测试桌面端使用的状态和配置接口。"""

    def test_任务和全局配置接口(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            config_path.write_text(
                "system:\n"
                "  shutdown_on_complete: false\n"
                "  server_chan_key: secret-value\n"
                "tasks:\n"
                "  demo:\n"
                "    enabled: true\n"
                "    interval_hours: 1\n",
                encoding="utf-8",
            )
            config = Config.load(config_path).model_copy(
                update={"db_path": root / "state.json", "log_dir": root / "logs"}
            )
            app = create_app(Scheduler(config, auto_shutdown=False))
            client = TestClient(app, client=("127.0.0.1", 12345))

            response = client.get("/api/status")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["system"]["server_chan_key_configured"])
            self.assertNotIn("secret-value", response.text)
            revision = payload["config_revision"]

            response = client.patch(
                "/api/tasks/demo",
                json={"interval_hours": 2, "config_revision": revision},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["task"]["interval_hours"], 2)

            response = client.patch(
                "/api/config/system",
                json={
                    "log_level": "DEBUG",
                    "shutdown_delay_seconds": 20,
                    "config_revision": response.json()["task"].get("config_revision", ""),
                },
            )
            self.assertIn(response.status_code, {200, 409})
            if response.status_code == 409:
                latest = client.get("/api/status").json()["config_revision"]
                response = client.patch(
                    "/api/config/system",
                    json={"log_level": "DEBUG", "shutdown_delay_seconds": 20, "config_revision": latest},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["system"]["log_level"], "DEBUG")

            response = client.patch(
                "/api/tasks/demo",
                json={"interval_hours": 3, "config_revision": revision},
            )
            self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
