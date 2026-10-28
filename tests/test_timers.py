"""Timers: set, pause, resume, expire (announced once), dismiss, survive a restart."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import timers


class TimerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "timers.json"
        p = patch.object(timers, "_TIMERS_FILE", self.file)
        p.start()
        self.addCleanup(p.stop)
        self.now = 1_000_000.0
        clock = patch.object(timers.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = timers._TimerStore()

    def _only(self) -> timers.Timer:
        (timer,) = self.store.list_active()
        return timer

    def test_set_and_expire_announces_once(self) -> None:
        self.assertIn("Timer set for 5m: tea.", self.store.set_timer(300, " tea "))
        self.assertEqual(self.store.pop_newly_expired(), [])
        self.now += 301
        (expired,) = self.store.pop_newly_expired()
        self.assertTrue(expired.alarming)
        self.assertEqual(self.store.pop_newly_expired(), [])  # announced already
        self.assertIn("dismissed", self.store.dismiss(expired.id))
        self.assertEqual(self.store.list_active(), [])

    def test_refusals(self) -> None:
        self.assertTrue(self.store.set_timer(0, "x").startswith("Refused"))
        self.assertTrue(self.store.cancel("  ").startswith("Refused"))
        self.assertIn("No timer", self.store.cancel("nope"))
        self.assertIn("No active timer", self.store.pause("nope"))
        self.assertIn("No active timer", self.store.resume("nope"))

    def test_pause_freezes_the_countdown(self) -> None:
        self.store.set_timer(120, "pasta")
        timer = self._only()
        self.now += 30
        self.assertIn("1m 30s remaining", self.store.pause(timer.id))
        self.assertIn("already paused", self.store.pause(timer.id))
        self.now += 3600  # paused: nothing expires
        self.assertEqual(self.store.pop_newly_expired(), [])
        self.assertEqual(self._only().remaining_seconds(), 90)
        self.assertIn("1m 30s remaining", self.store.resume(timer.id))
        self.assertIn("not paused", self.store.resume(timer.id))
        self.now += 91
        self.assertEqual(len(self.store.pop_newly_expired()), 1)
        self.assertIn("dismiss it instead", self.store.pause(timer.id))

    def test_timers_survive_a_restart_and_lapsed_ones_alarm(self) -> None:
        self.store.set_timer(60, "eggs")
        self.store.set_timer(600, "laundry")
        self.now += 120  # JARVIS was closed while eggs ran out
        restored = timers._TimerStore()
        by_message = {t.message: t for t in restored.list_active()}
        self.assertTrue(by_message["eggs"].alarming)
        self.assertFalse(by_message["laundry"].alarming)

    def test_bad_lines_in_the_file_are_skipped(self) -> None:
        self.file.write_text(json.dumps({"timers": [
            {"id": "a", "expires_at": self.now + 60, "message": "ok"},
            {"id": "b"},  # no expiry
            {"id": "c", "expires_at": "soon"},
        ]}))
        with self.assertLogs("jarvis.brain.json_store", level="WARNING"):
            store = timers._TimerStore()
        self.assertEqual([t.id for t in store.list_active()], ["a"])
        self.file.write_text("{not json")
        with self.assertLogs("jarvis.brain.json_store", level="WARNING"):
            self.assertEqual(timers._TimerStore().list_active(), [])

    def test_spoken_summary(self) -> None:
        with patch.object(timers, "_store", self.store):
            self.assertEqual(timers.list_timers(), "You have no active timers.")
            self.store.set_timer(3700, "oven")
            self.store.set_timer(45, "tea")
            tea = next(t for t in self.store.list_active() if t.message == "tea")
            self.store.pause(tea.id)
            summary = timers.list_timers()
        self.assertIn("tea - PAUSED, 45s remaining", summary)
        self.assertIn("oven in 1h 1m", summary)

    def test_durations(self) -> None:
        cases = {0: "0s", 59: "59s", 60: "1m", 61: "1m 1s", 3600: "1h", 3660: "1h 1m", -5: "0s"}
        for seconds, text in cases.items():
            self.assertEqual(timers._format_duration(seconds), text)


if __name__ == "__main__":
    unittest.main()
