"""Habit learning: bounded event log and derived digest."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from brain import habits


class HabitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_file = habits._HABITS_FILE
        habits._HABITS_FILE = Path(self.tempdir.name) / "habits.json"
        habits._cache_key = None
        habits._cache_value = ""

    def tearDown(self) -> None:
        habits._HABITS_FILE = self.old_file
        habits._cache_key = None
        self.tempdir.cleanup()

    def test_record_turn_keeps_targets_and_drops_meta_tools(self) -> None:
        at = datetime(2026, 9, 1, 20, 30).astimezone()
        habits.record_turn([
            ("play_music", {"query": "  Lofi   Beats "}),
            ("remember_fact", {"content": "x"}),
            ("play_music", {"query": "lofi beats"}),
            ("get_weather", {}),
        ], at=at)
        data = json.loads(habits._HABITS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["turns"]), 1)
        turn = data["turns"][0]
        self.assertEqual(turn["h"], 20)
        self.assertEqual(turn["tools"], [["play_music", "lofi beats"], ["get_weather", ""]])

    def test_log_is_bounded(self) -> None:
        with patch.object(habits, "_MAX_TURNS", 3):
            for _ in range(5):
                habits.record_turn([])
        data = json.loads(habits._HABITS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["turns"]), 3)

    def test_summary_describes_time_tools_and_frequent_targets(self) -> None:
        now = datetime(2026, 9, 1, 21, 0).astimezone()
        turns = []
        for index in range(12):
            moment = now - timedelta(days=index % 5)
            tools = [["play_music", "lofi beats"]] if index < 5 else [["control_device", "office lights off"]]
            turns.append({"t": moment.isoformat(timespec="minutes"), "h": 20, "d": moment.weekday(), "tools": tools})
        lines = habits.summarize(turns, now=now)
        joined = "\n".join(lines)
        self.assertIn("evening", joined)
        self.assertIn("music (5)", joined)
        self.assertIn("home control (7)", joined)
        self.assertIn('plays "lofi beats" (5x, usually evening)', joined)
        self.assertIn('asks for "office lights off" (7x, usually evening)', joined)

    def test_block_is_empty_until_enough_turns_then_cached(self) -> None:
        with patch.dict(os.environ, {"HABITS_MIN_TURNS": "3"}):
            habits.record_turn([("open_app", {"name": "Cursor"})])
            self.assertEqual(habits.render_habits_block(), "")
            self.assertIn("Not enough history", habits.describe_habits())
            habits.record_turn([("open_app", {"name": "Cursor"})])
            habits.record_turn([("open_app", {"name": "Cursor"})])
            block = habits.render_habits_block()
            self.assertIn('opens "cursor" (3x', block)
            self.assertIn("app launching (3)", block)
            self.assertIs(habits.render_habits_block(), block)
            self.assertIn("What I've noticed", habits.describe_habits())


if __name__ == "__main__":
    unittest.main()
