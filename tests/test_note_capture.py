"""Quick capture and the daily journal in the Obsidian vault."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import brain.knowledge_vault as vault
from brain.tools import execute_tool, get_tool_specs


class NoteCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "vault"
        self.root.mkdir()
        self.env = patch.dict(os.environ, {
            "OBSIDIAN_VAULT_PATH": str(self.root),
            "OBSIDIAN_INDEX_FOLDERS": "Jarvis Memory",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tempdir.cleanup()

    def test_journal_entries_group_under_sections_in_one_daily_note(self) -> None:
        at = datetime(2026, 9, 1, 9, 5).astimezone()
        vault.append_journal_entry("Buy filament", section="Notes", at=at)
        vault.append_journal_entry("Added Dune to Radarr.", section="Log", at=at.replace(hour=10))
        path = vault.append_journal_entry("Call the plumber", section="Notes", at=at.replace(hour=11))
        self.assertEqual(path, self.root / "Jarvis Memory" / "Journal" / "2026-09-01.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("# Tuesday, September 1, 2026", text)
        notes = text.index("## Notes")
        log = text.index("## Log")
        self.assertLess(notes, log)
        self.assertLess(text.index("09:05 — Buy filament"), text.index("11:05 — Call the plumber"))
        self.assertLess(text.index("11:05 — Call the plumber"), log)
        self.assertIn("\n\n## Log\n", text)
        self.assertIn("10:05 — Added Dune to Radarr.", text)

    def test_capture_without_title_goes_to_journal_and_is_searchable(self) -> None:
        result = vault.capture_note("Garage door code idea: use the spare opener")
        self.assertIn("Captured in today's journal", result)
        results = vault.search_vault("spare opener garage")
        self.assertTrue(results)
        self.assertTrue(results[0]["source"].startswith("Jarvis Memory/Journal/"))

    def test_titled_capture_creates_then_appends_a_note(self) -> None:
        first = vault.capture_note("Ryzen 9, 64GB RAM", title="PC Build: Plan/2027")
        path = self.root / "Jarvis Memory" / "Notes" / "PC Build Plan 2027.md"
        self.assertIn("Created note", first)
        self.assertTrue(path.exists())
        second = vault.capture_note("Add a 4TB NVMe", title="PC Build: Plan/2027")
        self.assertIn("Added to note", second)
        text = path.read_text(encoding="utf-8")
        self.assertIn("# PC Build Plan 2027", text)
        self.assertIn("Ryzen 9", text)
        self.assertIn("4TB NVMe", text)

    def test_folder_settings_cannot_escape_the_vault(self) -> None:
        with patch.dict(os.environ, {"OBSIDIAN_JOURNAL_FOLDER": "../outside"}):
            path = vault.append_journal_entry("x", at=datetime(2026, 9, 1, 8, 0).astimezone())
        self.assertEqual(path.parent, self.root / "Jarvis Memory" / "Journal")

    def test_tools_are_registered(self) -> None:
        names = {item.get("name") for item in get_tool_specs()}
        self.assertTrue({"capture_note", "observed_habits"}.issubset(names))
        self.assertIn("missing 'text'", execute_tool("capture_note", {}))
        self.assertIn("journal", execute_tool("capture_note", {"text": "remember the milk"}))


if __name__ == "__main__":
    unittest.main()
