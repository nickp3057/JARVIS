"""Tool handlers Claude calls, through execute_tool (the real entry point):
argument checks, time maths, and what each says back."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from brain import reminders, timers, tools, wake_alarm


def run(name: str, **args) -> str:  # noqa: ANN003
    with patch.object(tools.tool_health, "record"):
        return tools.execute_tool(name, args)


class ReminderAndTimerToolTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(reminders, "_REMINDERS_FILE", Path(tmp.name) / "r.json").start()
        patch.object(timers, "_TIMERS_FILE", Path(tmp.name) / "t.json").start()
        patch.object(reminders, "_store", reminders._ReminderStore()).start()
        patch.object(timers, "_store", timers._TimerStore()).start()
        self.addCleanup(patch.stopall)

    def test_reminder_forms(self) -> None:
        self.assertTrue(run("add_reminder", text="milk").startswith("Added to your list: milk."))
        reply = run("add_reminder", text="dentist", due_iso="2026-10-30T15:05")
        self.assertIn("Reminder set for", reply)
        (dentist,) = [r for r in reminders.active_reminders() if r.text == "dentist"]
        self.assertEqual(dentist.due_at, datetime(2026, 10, 30, 15, 5).timestamp())
        before = datetime.now().timestamp()
        run("add_reminder", text="bins", in_minutes=30, in_hours=1)
        (bins,) = [r for r in reminders.active_reminders() if r.text == "bins"]
        self.assertAlmostEqual(bins.due_at - before, 5400, delta=5)

    def test_reminder_refusals(self) -> None:
        self.assertEqual(run("add_reminder", text="  "), "Tool error: missing reminder text.")
        self.assertIn("must look like", run("add_reminder", text="x", due_iso="next tuesday"))
        self.assertTrue(run("add_reminder", text="x", in_minutes=0).startswith("Added to your list"))

    def test_timer_units_add_up(self) -> None:
        reply = run("set_timer", duration_hours=1, duration_minutes=1, duration_seconds=30, message="oven")
        self.assertTrue(reply.startswith("Timer set for 1h 1m: oven."), reply)  # spoken without the seconds
        (timer,) = timers.active_timers()
        self.assertAlmostEqual(timer.remaining_seconds(), 3690, delta=2)
        self.assertEqual(run("set_timer"), "Tool error: timer duration must be positive.")
        with self.assertLogs("jarvis.brain.tools", level="ERROR"):
            self.assertIn("Tool error while running set_timer", run("set_timer", duration_minutes="soon"))


class WakeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(wake_alarm, "_FILE", Path(tmp.name) / "wake.json")
        p.start()
        self.addCleanup(p.stop)

    def test_set_get_skip_cancel(self) -> None:
        self.assertTrue(run("set_wake_up", time="7:30am", days="weekdays", music="Back in Black").startswith("Done."))
        self.assertIn("7:30", run("get_wake_up"))
        self.assertIn("No alarm", run("skip_wake_up", day="tomorrow"))
        self.assertIn("back to normal", run("skip_wake_up", day="tomorrow", restore=True))
        self.assertIn("Nothing changed", run("skip_wake_up", day="someday"))
        self.assertIn("No alarm was changed", run("set_wake_up", time="25:00"))
        self.assertEqual(run("cancel_wake_up"), "Wake-up alarm cancelled, sir.")
        self.assertIn("no wake-up to cancel", run("cancel_wake_up"))


class SmallToolTests(unittest.TestCase):
    def test_time_reads_naturally(self) -> None:
        fixed = datetime(2026, 10, 28, 9, 5)
        with patch.object(tools, "datetime") as clock:
            clock.now.return_value = fixed
            self.assertEqual(run("get_time"), "It is 9:05 AM on Wednesday, October 28.")

    def test_devices_and_control(self) -> None:
        with patch.object(tools, "list_entities", return_value=(True, ["light.a", "light.b"])):
            self.assertEqual(run("list_devices", domain="light"), "light.a, light.b")
        with patch.object(tools, "list_entities", return_value=(True, [])):
            self.assertEqual(run("list_devices", domain="fan"), "No fan entities found.")
        with patch.object(tools, "list_entities", return_value=(False, "offline")):
            self.assertEqual(run("list_devices"), "Tool error: offline")
        self.assertEqual(run("control_device"), "Tool error: missing 'command'.")
        with patch.object(tools, "handle_device_command", return_value="Turn On command sent.") as control:
            self.assertEqual(run("control_device", command="turn on the lamp"), "Turn On command sent.")
        control.assert_called_once_with("turn on the lamp")

    def test_volume(self) -> None:
        self.assertIn("missing 'percent'", run("set_volume"))
        with patch.object(tools.spotify, "set_volume", return_value="Volume 40.") as volume:
            self.assertEqual(run("set_volume", percent="40"), "Volume 40.")
        volume.assert_called_once_with(40)


if __name__ == "__main__":
    unittest.main()
