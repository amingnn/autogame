"""测试计划任务时间列表处理。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from autogame.automation.scheduler import get_scheduled_times, set_scheduled_times


class SchedulerTests(unittest.TestCase):
    def test_reads_and_sorts_script_time_list(self) -> None:
        with patch(
            "autogame.automation.scheduler._run_script",
            return_value='["19:00", "07:30"]',
        ) as run_script:
            self.assertEqual(get_scheduled_times(), ["07:30", "19:00"])
        run_script.assert_called_once_with("-List")

    def test_saves_multiple_unique_times_through_existing_script(self) -> None:
        with patch(
            "autogame.automation.scheduler._run_script",
            side_effect=["updated", '["07:30", "19:00"]'],
        ) as run_script:
            result = set_scheduled_times(["19:00", "07:30", "19:00"])

        self.assertEqual(result, ["07:30", "19:00"])
        self.assertEqual(
            run_script.call_args_list[0].args,
            ("-TimesCsv", "07:30,19:00"),
        )
        self.assertEqual(run_script.call_args_list[1].args, ("-List",))

    def test_rejects_empty_or_invalid_time_without_running_script(self) -> None:
        with patch("autogame.automation.scheduler._run_script") as run_script:
            for times in ([], ["25:61"]):
                with self.subTest(times=times), self.assertRaises(ValueError):
                    set_scheduled_times(times)
        run_script.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
