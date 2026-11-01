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

    def test_knowledge_lists_and_reads_authorized_notes_only(self) -> None:
        vault = Path(self.tempdir.name) / "vault"
        (vault / "Jarvis Memory" / "Journal").mkdir(parents=True)
        (vault / "Private").mkdir()
        (vault / "Jarvis Memory" / "Profile.md").write_text(
            "---\njarvis_managed: true\n---\n\n# JARVIS Profile Memory\n\n## music\n\n"
            "- Likes AC/DC <!-- jarvis:abc -->\n  - Metadata: key=music, confidence=1.0\n")
        (vault / "Jarvis Memory" / "Journal" / "2026-10-31.md").write_text("# Oct 31\n- Went out\n")
        (vault / "Private" / "Secret.md").write_text("nope")
        with patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(vault), "OBSIDIAN_INDEX_FOLDERS": "Jarvis Memory"}):
            self.assertEqual(self.client.get("/api/knowledge").status_code, 401)
            notes = self.client.get("/api/knowledge", headers=self.headers).json()["notes"]
            self.assertEqual([n["path"] for n in notes],
                             ["Jarvis Memory/Profile.md", "Jarvis Memory/Journal/2026-10-31.md"])
            note = self.client.get("/api/knowledge/note", params={"path": "Jarvis Memory/Profile.md"},
                                   headers=self.headers).json()
            self.assertEqual(note["text"], "# JARVIS Profile Memory\n\n## music\n\n- Likes AC/DC\n")
            for bad in ("Private/Secret.md", "../vault/Private/Secret.md"):
                self.assertEqual(self.client.get("/api/knowledge/note", params={"path": bad},
                                                 headers=self.headers).status_code, 404)

    def test_knowledge_graph_maps_folders_topics_and_wikilinks(self) -> None:
        vault = Path(self.tempdir.name) / "vault"
        (vault / "Jarvis Memory" / "Notes").mkdir(parents=True)
        (vault / "Private").mkdir()
        (vault / "Jarvis Memory" / "Profile.md").write_text(
            "# Profile\n\n## music\n\n- Likes AC/DC\n- Hates jazz\n\n## school\n\n- Calc II\n")
        (vault / "Jarvis Memory" / "Notes" / "Plan.md").write_text("See [[Profile|me]] and [[Secret]].")
        (vault / "Private" / "Secret.md").write_text("nope")
        with patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(vault), "OBSIDIAN_INDEX_FOLDERS": "Jarvis Memory"}):
            self.assertEqual(self.client.get("/api/knowledge/graph").status_code, 401)
            graph = self.client.get("/api/knowledge/graph", headers=self.headers).json()
        ids = {n["id"]: n for n in graph["nodes"]}
        self.assertNotIn("Private/Secret.md", ids)
        self.assertEqual(ids["topic:music"]["weight"], 2)
        self.assertEqual(ids["topic:school"]["weight"], 1)
        edges = {(e["from"], e["to"], e["kind"]) for e in graph["edges"]}
        self.assertIn(("Jarvis Memory/Notes/Plan.md", "Jarvis Memory/Profile.md", "link"), edges)
        self.assertIn(("Jarvis Memory/Notes/Plan.md", "folder:Jarvis Memory/Notes", "tree"), edges)
        self.assertIn(("folder:Jarvis Memory/Notes", "folder:Jarvis Memory", "tree"), edges)
        self.assertIn(("folder:Jarvis Memory", "root", "tree"), edges)
        self.assertEqual(sum(1 for e in edges if e[2] == "link"), 1)

    def test_knowledge_import_saves_a_searchable_note(self) -> None:
        vault = Path(self.tempdir.name) / "vault"
        with patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(vault), "OBSIDIAN_INDEX_FOLDERS": "Jarvis Memory"}):
            self.assertEqual(self.client.post("/api/knowledge/import", json={"text": "x"}).status_code, 401)
            reply = self.client.post("/api/knowledge/import", headers=self.headers,
                                     json={"title": "From Claude/GPT", "text": "## Food\nLoves ramen."})
            self.assertEqual(reply.json()["path"], "Jarvis Memory/Imported/From Claude GPT.md")
            from brain import knowledge_vault
            self.assertIn("ramen", knowledge_vault.search_vault("ramen")[0]["excerpt"])
            self.assertEqual(self.client.post("/api/knowledge/import", headers=self.headers,
                                              json={"text": "   "}).status_code, 422)

    def test_requests_are_logged_as_traffic_by_device(self) -> None:
        from brain import traffic
        start = traffic.seq()
        self.client.get("/health", headers={**self.headers, "X-Jarvis-Device": "ipad"})
        self.client.get("/health", headers=self.headers)
        self.client.get("/health")  # no token: not a signal from anyone
        logged = [(t["from"], t["to"], t["label"]) for t in traffic.since(start)]
        self.assertEqual(logged, [("ipad", "core", "health"), ("core", "ipad", "health"),
                                  ("iphone", "core", "health"), ("core", "iphone", "health")])

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

    def test_media_diagnose_is_authenticated_and_read_only(self) -> None:
        self.assertEqual(self.client.get("/api/status/media/diagnose").status_code, 401)
        report = {"stage": "download", "stalled": True, "summary": "stuck", "item": None,
                  "steps": [], "fixes": [], "candidates": []}
        with patch.object(server.media_doctor, "diagnose", return_value=report) as diagnose:
            response = self.client.get("/api/status/media/diagnose?title=Dune&kind=movie", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], "stuck")
        diagnose.assert_called_once_with("Dune", "movie")

    def test_capabilities_are_authenticated_and_versioned(self) -> None:
        self.assertEqual(self.client.get("/api/capabilities").status_code, 401)
        response = self.client.get("/api/capabilities", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["api_version"], 1)
        self.assertTrue(payload["capabilities"]["chat"]["streaming"])
        self.assertTrue(payload["capabilities"]["actions"]["home_on_off"])
        self.assertTrue(payload["capabilities"]["dashboard"]["academic_countdowns"])
        self.assertTrue(payload["capabilities"]["dashboard"]["media_storage"])

    def test_stream_binds_and_persists_session(self) -> None:
        with self.client.stream(
            "POST", "/chat/stream", headers=self.headers, json={"message": "hello"}
        ) as response:
            self.assertEqual(response.status_code, 200)
            session_id = response.headers["x-jarvis-session-id"]
            # Without nosniff, iOS buffers the first 512 bytes (most replies).
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.read().decode(), "Stream: hello")

        transcript = self.client.get(f"/sessions/{session_id}", headers=self.headers).json()
        self.assertEqual(transcript["messages"][-1]["content"], "Stream: hello")

    def test_stream_segments_are_opt_in(self) -> None:
        with self.client.stream("POST", "/chat/stream", headers=self.headers, json={"message": "a"}) as response:
            self.assertEqual(response.headers["x-jarvis-stream-segments"], "0")
            response.read()
        headers = {**self.headers, "X-Jarvis-Stream-Segments": "1"}
        with self.client.stream("POST", "/chat/stream", headers=headers, json={"message": "b"}) as response:
            self.assertEqual(response.headers["x-jarvis-stream-segments"], "1")
            response.read()

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
