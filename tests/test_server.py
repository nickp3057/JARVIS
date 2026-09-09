import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import brain.history as history
import brain.debug_reports as debug_reports


class FakeRuntime:
    brain_online = True
    brain_label = "Test brain"

    def delete_companion_session(self, session_id):
        return history.delete_session(session_id)

    def start_session(self) -> str:
        return history.start_new_session()

    def respond_in_session(self, session_id: str, message: str) -> str:
        reply = f"Reply: {message}"
        history.append_turn(message, reply, session_id)
        return reply

    def respond_streaming_in_session(self, session_id: str, message: str):
        reply = f"Stream: {message}"
        yield "Stream: "
        yield message
        history.append_turn(message, reply, session_id)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = history._DB_PATH
        self.old_reports_path = debug_reports._REPORTS_FILE
        history._DB_PATH = Path(self.tempdir.name) / "history.db"
        debug_reports._REPORTS_FILE = Path(self.tempdir.name) / "debug_reports.json"
        server._runtime = FakeRuntime()
        self.token_patch = patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"})
        self.token_patch.start()
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.client.close()
        self.token_patch.stop()
        server._runtime = None
        history._DB_PATH = self.old_db_path
        debug_reports._REPORTS_FILE = self.old_reports_path
        self.tempdir.cleanup()

    def test_auth_sessions_chat_history_rename_and_delete(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 401)
        health = self.client.get("/health", headers=self.headers)
        self.assertEqual(health.status_code, 200)

        created = self.client.post("/sessions", headers=self.headers)
        self.assertEqual(created.status_code, 201)
        session_id = created.json()["session_id"]

        reply = self.client.post(
            "/chat",
            headers=self.headers,
            json={"message": "hello", "session_id": session_id},
        )
        self.assertEqual(reply.json(), {"reply": "Reply: hello", "session_id": session_id})

        transcript = self.client.get(f"/sessions/{session_id}", headers=self.headers).json()
        self.assertEqual([item["role"] for item in transcript["messages"]], ["user", "assistant"])

        renamed = self.client.patch(
            f"/sessions/{session_id}", headers=self.headers, json={"title": "Greeting"}
        )
        self.assertEqual(renamed.status_code, 204)
        sessions = self.client.get("/sessions", headers=self.headers).json()["sessions"]
        self.assertEqual(sessions[0]["title"], "Greeting")

        deleted = self.client.delete(f"/sessions/{session_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(
            self.client.get(f"/sessions/{session_id}", headers=self.headers).status_code,
            404,
        )

    def test_stream_binds_and_persists_session(self) -> None:
        with self.client.stream(
            "POST", "/chat/stream", headers=self.headers, json={"message": "hello"}
        ) as response:
            self.assertEqual(response.status_code, 200)
            session_id = response.headers["x-jarvis-session-id"]
            self.assertEqual(response.read().decode(), "Stream: hello")

        transcript = self.client.get(f"/sessions/{session_id}", headers=self.headers).json()
        self.assertEqual(transcript["messages"][-1]["content"], "Stream: hello")

    def test_submit_debug_report_requires_auth_and_persists_context(self) -> None:
        payload = {
            "issue": "The answer ignored my correction.",
            "session_id": "session-42",
            "conversation_title": "Lamp problem",
            "focus_message_id": "assistant-1",
            "messages": [
                {"id": "user-1", "role": "user", "content": "Turn off the lamp."},
                {"id": "assistant-1", "role": "assistant", "content": "Lamp turned on."},
                {"id": "user-2", "role": "user", "content": "Dude, wrong direction."},
            ],
            "app_context": {"app_version": "1.6", "connection": "online"},
        }
        self.assertEqual(self.client.post("/debug-reports", json=payload).status_code, 401)
        response = self.client.post("/debug-reports", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 201)
        report = debug_reports.get_debug_report(response.json()["id"])
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report["messages"][-1]["content"], "Dude, wrong direction.")
        self.assertEqual(report["focus_message_id"], "assistant-1")

    def test_submit_debug_report_validates_payload(self) -> None:
        empty_issue = {
            "issue": "",
            "messages": [{"role": "user", "content": "hello"}],
        }
        self.assertEqual(
            self.client.post("/debug-reports", headers=self.headers, json=empty_issue).status_code,
            422,
        )
        bad_role = {
            "issue": "bad",
            "messages": [{"role": "system", "content": "hello"}],
        }
        self.assertEqual(
            self.client.post("/debug-reports", headers=self.headers, json=bad_role).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
