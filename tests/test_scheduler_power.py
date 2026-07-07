import tempfile
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from core.common import Config, SystemConfig, TaskConfig
from core.scheduler import Scheduler


class SchedulerPowerTests(unittest.TestCase):
    def test_completion_action_sleep_uses_windows_suspend_command_and_requests_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(
                db_path=Path(tmp) / "state.json",
                system=SystemConfig(
                    shutdown_on_complete=True,
                    shutdown_delay_seconds=0,
                    completion_action="sleep",
                ),
                tasks={
                    "maa": TaskConfig(
                        enabled=True,
                        interval_hours=12,
                        entry="",
                        done_on="webhook",
                    )
                },
            )
            scheduler = Scheduler(config)

            with (
                patch("core.scheduler.platform.system", return_value="Windows"),
                patch("core.scheduler.os.system") as system,
                patch.object(scheduler, "_push_report"),
            ):
                scheduler.mark_done("maa")

            command = system.call_args.args[0]
            self.assertIn("SetSuspendState", command)
            self.assertNotIn("shutdown /s", command)
            self.assertTrue(scheduler.stop_requested)

    def test_completion_action_none_requests_stop_without_system_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(
                db_path=Path(tmp) / "state.json",
                system=SystemConfig(
                    shutdown_on_complete=True,
                    completion_action="none",
                ),
                tasks={
                    "maa": TaskConfig(
                        enabled=True,
                        interval_hours=12,
                        entry="",
                        done_on="webhook",
                    )
                },
            )
            scheduler = Scheduler(config)

            scheduler.mark_done("maa")

            self.assertTrue(scheduler.stop_requested)


class SchedulerPollLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_loop_triggers_completion_when_all_enabled_tasks_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(
                db_path=Path(tmp) / "state.json",
                system=SystemConfig(shutdown_on_complete=True),
                tasks={
                    "maa": TaskConfig(
                        enabled=True,
                        interval_hours=12,
                        entry="",
                        done_on="webhook",
                    )
                },
            )
            config.db_path.write_text(
                json.dumps({"maa": datetime.now(tz=timezone.utc).isoformat()}),
                encoding="utf-8",
            )
            scheduler = Scheduler(config)

            with patch.object(scheduler, "_trigger_shutdown") as trigger_shutdown:
                await scheduler.poll_loop()

            trigger_shutdown.assert_called_once()


class ConfigExampleTests(unittest.TestCase):
    def test_maa_task_has_start_entry(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config.example.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        self.assertEqual(data["tasks"]["maa"]["entry"], "maa.run")


if __name__ == "__main__":
    unittest.main()
