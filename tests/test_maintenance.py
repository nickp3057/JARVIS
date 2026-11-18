import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import app.status_hub as status_hub
import brain.maintenance as maintenance
from brain.tools import execute_tool


class MaintenanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_path = maintenance._DB_PATH
        maintenance._DB_PATH = Path(self.tempdir.name) / "maintenance.db"

    def tearDown(self) -> None:
        maintenance._DB_PATH = self.old_path
        self.tempdir.cleanup()


class DateMathTests(unittest.TestCase):
    def test_add_months_clamps_day(self) -> None:
        self.assertEqual(maintenance.add_months(date(2026, 8, 31), 6), date(2027, 2, 28))
        self.assertEqual(maintenance.add_months(date(2027, 8, 31), 6), date(2028, 2, 29))
        self.assertEqual(maintenance.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_interval_never_done_is_due_today(self) -> None:
        task = {"schedule_type": "interval", "interval_value": 3, "interval_unit": "months"}
        today = date(2026, 10, 27)
        self.assertEqual(maintenance.next_due(task, None, today), today)
        self.assertEqual(maintenance.next_due(task, date(2026, 10, 1), today), date(2027, 1, 1))

    def test_fixed_calendar_overdue_early_and_next_year(self) -> None:
        task = {"schedule_type": "fixed_calendar", "fixed_month": 11, "fixed_day": 1,
                "created_at": "2026-01-01T00:00:00"}
        # Upcoming, not yet done.
        self.assertEqual(maintenance.next_due(task, None, date(2026, 10, 27)), date(2026, 11, 1))
        # Done a week early counts for this year.
        self.assertEqual(maintenance.next_due(task, date(2026, 10, 25), date(2026, 10, 27)), date(2027, 11, 1))
        # Missed: stays overdue after the date passes.
        self.assertEqual(maintenance.next_due(task, None, date(2026, 11, 20)), date(2026, 11, 1))
        # Done late: next year.
        self.assertEqual(maintenance.next_due(task, date(2026, 11, 20), date(2026, 11, 21)), date(2027, 11, 1))
        # A task created after this year's date isn't overdue for it.
        fresh = {**task, "created_at": "2026-11-10T00:00:00"}
        self.assertEqual(maintenance.next_due(fresh, None, date(2026, 11, 20)), date(2027, 11, 1))

    def test_schedule_labels(self) -> None:
        self.assertEqual(maintenance.schedule_label(
            {"schedule_type": "interval", "interval_value": 3, "interval_unit": "months"}), "Every quarter")
        self.assertEqual(maintenance.schedule_label(
            {"schedule_type": "interval", "interval_value": 1, "interval_unit": "years"}), "Every year")
        self.assertEqual(maintenance.schedule_label(
            {"schedule_type": "fixed_calendar", "fixed_month": 4, "fixed_day": None}), "Every Apr")


class StoreTests(MaintenanceTestCase):
    def test_seeds_once_and_never_return(self) -> None:
        names = [t["name"] for t in maintenance.list_tasks()]
        self.assertEqual(sorted(names), ["Replace car scent", "Rotate mattress", "Trim bonsai"])
        bonsai = maintenance.find_task("bonsai")
        self.assertEqual(bonsai["schedule_label"], "Every quarter")
        maintenance.delete_task(bonsai["id"])
        maintenance._schema_ready_paths.clear()
        self.assertIsNone(maintenance.find_task("bonsai"))

    def test_complete_resets_countdown_and_history_and_undo(self) -> None:
        mattress = maintenance.find_task("mattress")
        self.assertEqual(mattress["status"], "due")
        done = maintenance.complete_task(mattress["id"], "flipped too")
        self.assertEqual(done["status"], "ok")
        self.assertEqual(done["last_note"], "flipped too")
        self.assertGreater(done["days_remaining"], 150)
        entries = maintenance.history(mattress["id"])
        self.assertEqual(len(entries), 1)
        self.assertTrue(maintenance.delete_log_entry(mattress["id"], entries[0]["id"]))
        self.assertEqual(maintenance.get_task(mattress["id"])["status"], "due")

    def test_backdated_create_and_complete(self) -> None:
        last = date.today() - timedelta(days=20)
        task = maintenance.create_task({"name": "Change HVAC filter", "schedule_type": "interval",
                                        "interval_value": 30, "interval_unit": "days",
                                        "last_done": last.isoformat()})
        self.assertEqual(task["days_remaining"], 10)
        self.assertEqual(task["days_since"], 20)
        with self.assertRaises(ValueError):
            maintenance.complete_task(task["id"], on=(date.today() + timedelta(days=1)).isoformat())

    def test_log_only_and_sorting(self) -> None:
        maintenance.create_task({"name": "Descale kettle", "schedule_type": "log_only",
                                 "last_done": (date.today() - timedelta(days=4)).isoformat()})
        overdue = maintenance.create_task({"name": "Clean gutters", "schedule_type": "interval",
                                           "interval_value": 10, "interval_unit": "days",
                                           "last_done": (date.today() - timedelta(days=15)).isoformat()})
        tasks = maintenance.list_tasks()
        self.assertEqual(tasks[0]["id"], overdue["id"])
        self.assertEqual(tasks[0]["status"], "overdue")
        self.assertEqual(tasks[-1]["name"], "Descale kettle")
        self.assertIsNone(tasks[-1]["days_remaining"])
        self.assertEqual(tasks[-1]["status"], "log")
        due_names = [t["name"] for t in maintenance.due_within()]
        self.assertIn("Clean gutters", due_names)
        self.assertNotIn("Descale kettle", due_names)

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            maintenance.create_task({"name": "x", "schedule_type": "interval", "interval_value": 0,
                                     "interval_unit": "days"})
        with self.assertRaises(ValueError):
            maintenance.create_task({"name": "x", "schedule_type": "fixed_calendar", "fixed_month": 13})

    def test_tools(self) -> None:
        reply = execute_tool("log_maintenance", {"name": "car scent", "note": "vanilla"})
        self.assertIn("Logged Replace car scent", reply)
        self.assertIn("Replace car scent", execute_tool("list_maintenance", {}))
        added = execute_tool("add_maintenance_task", {"name": "Oil change", "schedule_type": "interval",
                                                      "interval_value": 6, "interval_unit": "months"})
        self.assertIn("Added Oil change", added)
        self.assertIn("couldn't pin down", execute_tool("log_maintenance", {"name": "nonsense"}))


class EndpointTests(MaintenanceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.token_patch = patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"})
        self.token_patch.start()
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.client.close()
        self.token_patch.stop()
        super().tearDown()

    def test_crud_complete_history(self) -> None:
        self.assertEqual(self.client.get("/api/maintenance").status_code, 401)
        tasks = self.client.get("/api/maintenance", headers=self.headers).json()["tasks"]
        self.assertEqual(len(tasks), 3)

        created = self.client.post("/api/maintenance", headers=self.headers, json={
            "name": "Flush water heater", "schedule_type": "fixed_calendar", "fixed_month": 4,
        })
        self.assertEqual(created.status_code, 200)
        task_id = created.json()["task"]["id"]
        self.assertEqual(self.client.post("/api/maintenance", headers=self.headers, json={
            "name": "Bad", "schedule_type": "interval"}).status_code, 422)

        done = self.client.post(f"/api/maintenance/{task_id}/complete", headers=self.headers,
                                json={"note": "sediment looked fine"})
        self.assertEqual(done.status_code, 200)
        self.assertIsNotNone(done.json()["task"]["last_done"])
        # Body is optional.
        self.assertEqual(self.client.post(f"/api/maintenance/{task_id}/complete",
                                          headers=self.headers).status_code, 200)

        history = self.client.get(f"/api/maintenance/{task_id}/history", headers=self.headers).json()
        self.assertEqual(len(history["history"]), 2)
        self.assertEqual(history["task"]["log_count"], 2)
        entry = history["history"][0]["id"]
        self.assertEqual(self.client.delete(f"/api/maintenance/{task_id}/history/{entry}",
                                            headers=self.headers).status_code, 204)

        updated = self.client.put(f"/api/maintenance/{task_id}", headers=self.headers, json={
            "name": "Flush water heater", "schedule_type": "interval", "interval_value": 1, "interval_unit": "years",
        })
        self.assertEqual(updated.json()["task"]["schedule_label"], "Every year")

        self.assertEqual(self.client.delete(f"/api/maintenance/{task_id}", headers=self.headers).status_code, 204)
        self.assertEqual(self.client.get(f"/api/maintenance/{task_id}/history",
                                         headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/api/maintenance/999/complete",
                                          headers=self.headers).status_code, 404)

    def test_todo_add_and_done_deletes(self) -> None:
        import brain.reminders as reminders
        with patch.object(reminders, "_REMINDERS_FILE", Path(self.tempdir.name) / "reminders.json"), \
                patch.object(reminders, "_store", reminders._ReminderStore()):
            self.assertEqual(self.client.post("/api/todos", headers=self.headers, json={"text": "  "}).status_code, 422)
            todo = self.client.post("/api/todos", headers=self.headers, json={"text": "Buy filters"}).json()["todo"]
            self.assertEqual(todo["text"], "Buy filters")
            self.assertEqual([r.text for r in reminders.active_reminders()], ["Buy filters"])
            renamed = self.client.put(f"/api/todos/{todo['id']}", headers=self.headers, json={"text": " Buy HEPA filters "})
            self.assertEqual(renamed.json()["todo"]["text"], "Buy HEPA filters")
            self.assertEqual(self.client.put("/api/todos/nope", headers=self.headers, json={"text": "x"}).status_code, 404)
            self.assertEqual(self.client.post(f"/api/todos/{todo['id']}/done", headers=self.headers).status_code, 204)
            self.assertEqual(reminders.active_reminders(include_done=True), [])
            self.assertEqual(self.client.post(f"/api/todos/{todo['id']}/done", headers=self.headers).status_code, 404)

    def test_now_feed_lists_due_tasks(self) -> None:
        status_hub.clear_cache()
        with patch.object(status_hub, "calendar_configured", return_value=False):
            snapshot = status_hub.now_status()
        status_hub.clear_cache()
        self.assertEqual({t["name"] for t in snapshot["maintenance"]},
                         {"Rotate mattress", "Replace car scent", "Trim bonsai"})


if __name__ == "__main__":
    unittest.main()
