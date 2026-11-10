import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import brain.donna as donna


class FakeClient:
    """Plays back scripted responses; records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        return self.responses.pop(0)


def text(t, stop="end_turn"):
    return {"content": [{"type": "text", "text": t}], "stop_reason": stop}


def tool(name, args, tid="t1"):
    return {"content": [{"type": "tool_use", "id": tid, "name": name, "input": args}], "stop_reason": "tool_use"}


class DonnaTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(donna, "_DB_PATH", Path(self.tmp.name) / "donna.db")
        self.patch.start()
        env = patch.dict("os.environ", {"DONNA_MODEL": "claude-sonnet-5-5", "DONNA_NOTES_MODEL": "claude-haiku-5-5"})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()


class ConversationTests(DonnaTestCase):
    def test_reply_runs_tools_and_keeps_the_session(self):
        client = FakeClient([tool("log_mood", {"score": 4, "note": "tired"}),
                             text("Four's honest. What's weighing most?")])
        out = donna.reply("rough week", client=client)
        self.assertEqual(out["reply"], "Four's honest. What's weighing most?")
        self.assertEqual(donna.moods()[0]["score"], 4)
        first = client.requests[0]
        self.assertEqual(first["model"], "claude-sonnet-5-5")
        self.assertIn("Donna", first["system"][0]["text"])
        self.assertIn("cache_control", first["system"][1])
        names = {t["name"] for t in first["tools"]}
        self.assertTrue({"recall_notes", "update_case_file", "web_search", "web_fetch"} <= names)
        web = [t for t in first["tools"] if t["name"] == "web_search"][0]
        self.assertIn("nhs.uk", web["allowed_domains"])
        # Second message: same session, history sent, nothing older.
        client2 = FakeClient([text("Go on.")])
        again = donna.reply("school mostly", client=client2)
        self.assertEqual(again["session_id"], out["session_id"])
        sent = client2.requests[0]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])

    def test_context_carries_case_file_notes_and_followups_only(self):
        donna.set_case_file("Nick is a student; brother Jake.")
        client = FakeClient([tool("add_followup", {"text": "Ask about the Jake call"}), text("Got it.")])
        donna.reply("I'll call Jake Friday", client=client)
        self.assertIn("brother Jake", client.requests[0]["system"][1]["text"])
        self.assertEqual(donna.open_followups()[0]["text"], "Ask about the Jake call")

    def test_end_session_writes_note_and_case_file(self):
        donna.reply("hi", client=FakeClient([text("Hey you.")]))
        notes = FakeClient([text("<note>Nick checked in briefly.</note><case_file>Nick: student.</case_file>")])
        self.assertTrue(donna.end_session(client=notes))
        self.assertEqual(notes.requests[0]["model"], "claude-haiku-5-5")
        self.assertEqual(donna.recent_notes()[0]["note"], "Nick checked in briefly.")
        self.assertEqual(donna.case_file(), "Nick: student.")
        self.assertIsNone(donna.current_session())

    def test_idle_session_is_closed_before_a_new_one(self):
        first = donna.reply("hi", client=FakeClient([text("Hey.")]))
        with donna._db() as conn:
            conn.execute("UPDATE sessions SET last_at = ?", (time.time() - donna.IDLE_END_S - 5,))
        client = FakeClient([text("<note>Short hello.</note><case_file>x</case_file>"), text("Welcome back.")])
        second = donna.reply("back again", client=client)
        self.assertNotEqual(second["session_id"], first["session_id"])
        self.assertEqual(donna.recent_notes()[0]["note"], "Short hello.")

    def test_checkin_and_recall(self):
        donna.reply("x", client=FakeClient([tool("schedule_checkin", {"when": "2999-01-01T09:00"}), text("Will do.")]))
        self.assertEqual(len(donna.pending_checkins()), 1)
        with donna._db() as conn:
            conn.execute("UPDATE sessions SET note = 'Talked about the internship offer.'")
        self.assertIn("internship", donna._tool_recall({"query": "internship"}))
        self.assertIn("No earlier", donna._tool_recall({"query": "volcano"}))

    def test_import_and_forget(self):
        client = FakeClient([text("Nick: works and studies; anxious about money.")])
        donna.import_history("A long earlier chat about money, school and family. " * 3, client=client)
        self.assertIn("money", donna.case_file())
        donna.forget_everything()
        self.assertEqual(donna.case_file(), "")


class RouteTests(DonnaTestCase):
    def test_routes(self):
        with patch.dict("os.environ", {"JARVIS_API_TOKEN": "t"}), \
                patch.object(donna, "_client", return_value=FakeClient([text("Hi Nick.")])):
            c = TestClient(server.app)
            h = {"Authorization": "Bearer t"}
            self.assertEqual(c.get("/api/donna").status_code, 401)
            self.assertIsNone(c.get("/api/donna", headers=h).json()["session"])
            out = c.post("/api/donna/message", headers=h, json={"text": "hello"}).json()
            self.assertEqual(out["reply"], "Hi Nick.")
            state = c.get("/api/donna", headers=h).json()
            self.assertEqual(len(state["session"]["messages"]), 2)
            self.assertEqual(c.get("/api/donna/memory", headers=h).json()["notes"], [])
            self.assertEqual(c.delete("/api/donna/memory", headers=h).status_code, 204)


if __name__ == "__main__":
    unittest.main()
