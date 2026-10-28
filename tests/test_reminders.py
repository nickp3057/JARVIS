"""Reminders and to-dos: ordering, due announcements (once), done, delete, restart."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from brain import json_store, reminders


class ReminderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "reminders.json"
        p = patch.object(reminders, "_REMINDERS_FILE", self.file)
        p.start()
        self.addCleanup(p.stop)
        self.now = 2_000_000_000.0
        clock = patch.object(reminders.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = reminders._ReminderStore()

    def test_timed_first_soonest_first_then_todos(self) -> None:
        self.store.add("milk")
        self.store.add("call mum", due_at=self.now + 600)
        self.store.add("bins", due_at=self.now + 60)
        self.assertEqual([r.text for r in self.store.list_items()], ["bins", "call mum", "milk"])
        self.assertEqual(self.store.add("  ").text, "Reminder")

    def test_due_reminders_announce_once_and_todos_never(self) -> None:
        self.store.add("milk")
        self.store.add("bins", due_at=self.now + 60)
        self.assertEqual(self.store.pop_due(), [])
        self.now += 61
        (due,) = self.store.pop_due()
        self.assertEqual(due.message, "bins")
        self.assertEqual(self.store.pop_due(), [])

    def test_complete_and_delete(self) -> None:
        item = self.store.add("milk")
        self.assertEqual(self.store.complete(item.id), "Marked done: milk")
        self.assertEqual(self.store.list_items(), [])
        self.assertEqual(len(self.store.list_items(include_done=True)), 1)
        self.assertTrue(self.store.complete("nope").startswith("No reminder"))
        self.assertTrue(self.store.delete(item.id))
        self.assertFalse(self.store.delete(item.id))

    def test_done_items_are_never_due(self) -> None:
        item = self.store.add("bins", due_at=self.now - 5)
        self.store.complete(item.id)
        self.assertEqual(self.store.pop_due(), [])

    def test_restart_keeps_everything_in_order(self) -> None:
        self.store.add("milk")
        self.store.add("bins", due_at=self.now + 60)
        self.file.write_text(json.dumps({"reminders": list(reversed(
            json.loads(self.file.read_text())["reminders"])) + [{"text": "no id"}]}))
        with self.assertLogs("jarvis.brain.json_store", level="WARNING"):
            restored = reminders._ReminderStore()
        self.assertEqual([r.text for r in restored.list_items()], ["bins", "milk"])

    def test_public_helpers(self) -> None:
        with patch.object(reminders, "_store", self.store):
            self.assertEqual(reminders.list_reminders(), "You have no reminders, sir.")
            todo = reminders.add_reminder("milk")
            due = datetime(2026, 10, 30, 15, 5).timestamp()
            reminders.add_reminder("dentist", due_at=due)
            summary = reminders.list_reminders()
            self.assertIn("dentist at 3:05 PM on Fri", summary)
            self.assertIn(f"({todo.id}) milk", summary)
            self.assertTrue(reminders.remove_reminder(todo.id))
            self.assertEqual(reminders.delete_reminder(todo.id), f"No reminder with id {todo.id}.")
        self.assertEqual(reminders._format_due(None), "no due time")


class JsonStoreTests(unittest.TestCase):
    def test_round_trip_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "things.json"
            self.assertEqual(json_store.load(path, "things", dict), [])
            json_store.save(path, "things", [{"a": 1}])
            self.assertEqual(json_store.load(path, "things", dict), [{"a": 1}])
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            path.write_text("[1, 2]")  # not an object: nothing, no crash
            self.assertEqual(json_store.load(path, "things", dict), [])
            with patch.object(Path, "replace", side_effect=OSError("disk full")), \
                    self.assertLogs("jarvis.brain.json_store", level="ERROR"):
                json_store.save(path, "things", [])


if __name__ == "__main__":
    unittest.main()
