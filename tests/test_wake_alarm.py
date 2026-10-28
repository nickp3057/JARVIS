"""Wake-up alarm: parsing, scheduling, once-a-day firing and the tools."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from brain import tools, wake_alarm


class WakeAlarmTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(wake_alarm, "_FILE", Path(tmp.name) / "wake_alarm.json")
        p.start()
        self.addCleanup(p.stop)

    def test_parsing(self) -> None:
        self.assertEqual(wake_alarm.parse_time("7"), "07:00")
        self.assertEqual(wake_alarm.parse_time("6:45 am"), "06:45")
        self.assertEqual(wake_alarm.parse_time("12:15am"), "00:15")
        self.assertEqual(wake_alarm.parse_time("7:30pm"), "19:30")
        with self.assertRaises(ValueError):
            wake_alarm.parse_time("25:00")
        self.assertEqual(wake_alarm.parse_days("weekdays"), [0, 1, 2, 3, 4])
        self.assertEqual(wake_alarm.parse_days("Mon, Wed and Fri"), [0, 2, 4])
        self.assertEqual(wake_alarm.normalize_music(
            "https://open.spotify.com/playlist/37i9dQZF1DX0UrRvztWcAU?si=abc"),
            "spotify:playlist:37i9dQZF1DX0UrRvztWcAU")
        self.assertEqual(wake_alarm.normalize_music("resume"), "")
        with self.assertRaises(ValueError):
            wake_alarm.normalize_music("my morning playlist")

    def test_fires_once_in_its_window_on_its_days(self) -> None:
        wake_alarm.set_alarm("7:00am", "weekdays")
        tuesday = datetime(2026, 10, 27, 7, 3)
        self.assertIsNotNone(wake_alarm.due(tuesday))
        self.assertIsNone(wake_alarm.due(tuesday))  # already fired today
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 28, 6, 59)))
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 28, 7, 11)))  # window missed
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 31, 7, 1)))  # Saturday
        self.assertEqual(wake_alarm.next_time(wake_alarm.load(), datetime(2026, 10, 30, 8, 0)),
                         datetime(2026, 11, 2, 7, 0))

    def test_tools(self) -> None:
        reply = tools.execute_tool("set_wake_up", {
            "time": "6:30am", "days": "weekdays", "label": "Morning Acoustic",
            "music": "https://open.spotify.com/playlist/37i9dQZF1DX0UrRvztWcAU"})
        self.assertIn("6:30 AM, weekdays, with Morning Acoustic", reply)
        self.assertEqual(wake_alarm.public()["days"], "weekdays")
        self.assertIn("Spotify link", tools.execute_tool("set_wake_up", {"time": "7", "music": "chill vibes"}))
        self.assertIn("cancelled", tools.execute_tool("cancel_wake_up", {}))
        self.assertIsNone(wake_alarm.public())


if __name__ == "__main__":
    unittest.main()
