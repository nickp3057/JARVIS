import json
import tempfile
import unittest
from pathlib import Path

import brain.debug_reports as reports


class DebugReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_path = reports._REPORTS_FILE
        reports._REPORTS_FILE = Path(self.tempdir.name) / "debug_reports.json"

    def tearDown(self) -> None:
        reports._REPORTS_FILE = self.old_path
        self.tempdir.cleanup()

    def test_create_list_and_inspect_report(self) -> None:
        created = reports.create_debug_report(
            issue="JARVIS turned on the wrong lamp.",
            messages=[
                {"id": "u1", "role": "user", "content": "Turn off my lamp.", "created_at": "now"},
                {"id": "a1", "role": "assistant", "content": "Kitchen lamp on.", "created_at": "now"},
                {"id": "u2", "role": "user", "content": "No, that's frustrating.", "created_at": "now"},
            ],
            session_id="session-1",
            focus_message_id="a1",
            app_context={"app_version": "1.6"},
        )
        self.assertEqual(created["status"], "open")
        stored = reports.list_debug_reports()
        self.assertEqual(stored[0]["session_id"], "session-1")
        detail = reports.describe_debug_report(created["id"])
        self.assertIn("[FOCUS]", detail)
        self.assertIn("that's frustrating", detail)
        self.assertIn("app_version=1.6", detail)

    def test_rejects_empty_issue_or_transcript(self) -> None:
        with self.assertRaises(ValueError):
            reports.create_debug_report(issue=" ", messages=[{"role": "user", "content": "hi"}])
        with self.assertRaises(ValueError):
            reports.create_debug_report(issue="bad", messages=[])

    def test_filters_invalid_messages_and_bounds_fields(self) -> None:
        created = reports.create_debug_report(
            issue="x" * 5_000,
            messages=[
                {"role": "system", "content": "secret instruction"},
                {"id": "x" * 200, "role": "user", "content": "y" * 25_000},
            ],
            app_context={str(index): "v" * 600 for index in range(30)},
        )
        stored = reports.get_debug_report(created["id"])
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(len(stored["issue"]), 4_000)
        self.assertEqual(len(stored["messages"]), 1)
        self.assertEqual(len(stored["messages"][0]["content"]), 20_000)
        self.assertEqual(len(stored["app_context"]), 20)

    def test_corrupt_store_recovers_with_new_report(self) -> None:
        reports._REPORTS_FILE.write_text("{broken", encoding="utf-8")
        created = reports.create_debug_report(
            issue="Streaming stopped.",
            messages=[{"role": "error", "content": "Connection lost"}],
        )
        data = json.loads(reports._REPORTS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["id"], created["id"])

    def test_missing_report_has_clear_result(self) -> None:
        self.assertIn("No debug report found", reports.describe_debug_report("missing"))
        self.assertIn("No companion debug reports", reports.summarize_debug_reports())


if __name__ == "__main__":
    unittest.main()
