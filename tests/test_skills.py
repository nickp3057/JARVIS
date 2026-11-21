import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server
from brain import confirm_gate, skill_drafter, skill_requests, skills, tools

DATE_NIGHT = {
    "name": "date_night",
    "description": "Movie scene, a playlist on the speaker, volume down.",
    "params": [{"name": "playlist", "description": "What to play"}],
    "steps": [
        {"tool": "activate_home_action", "input": {"kind": "scene", "name": "Movie"}},
        {"tool": "play_music", "input": {"query": "{playlist}"}},
        {"tool": "set_volume", "input": {"percent": 30}},
    ],
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(skills, "_FILE", Path(self.tmp.name) / "skills.json"),
                  patch.object(skill_requests, "_FILE", Path(self.tmp.name) / "requests.json")):
            p.start()
            self.addCleanup(p.stop)
        confirm_gate.clear_for_tests()


class SkillTests(_Base):
    def test_a_draft_does_nothing_until_approved_then_runs_its_steps(self):
        record = skills.propose(**DATE_NIGHT)
        self.assertEqual(record["status"], "draft")
        self.assertIn("don't have an active skill", skills.run("date_night", {"playlist": "jazz"}))
        self.assertEqual(skills.render_block(), "")
        skills.set_status(record["id"], "active")
        self.assertIn("date_night(playlist)", skills.render_block())
        calls = []
        with patch.object(tools, "execute_tool", side_effect=lambda name, args, sid=None: calls.append((name, args)) or "Done."):
            reply = skills.run("date_night", {"playlist": "jazz"}, "s1")
        self.assertEqual(calls[1], ("play_music", {"query": "jazz"}))
        self.assertEqual(len(calls), 3)
        self.assertIn("Ran the date_night skill", reply)
        self.assertEqual(skills.listing("active")[0]["uses"], 1)
        self.assertIn("needs: playlist", skills.run("date_night", {}))

    def test_a_failing_step_stops_the_rest(self):
        record = skills.propose(**DATE_NIGHT)
        skills.set_status(record["id"], "active")
        answers = iter(["Done.", "Tool error while running play_music: Spotify unreachable", "Done."])
        with patch.object(tools, "execute_tool", side_effect=lambda *a: next(answers)) as run:
            reply = skills.run("date_night", {"playlist": "x"})
        self.assertEqual(run.call_count, 2)
        self.assertIn("Stopped: step 2 failed", reply)

    def test_only_everyday_tools_and_declared_params(self):
        for bad, why in (
            ({**DATE_NIGHT, "steps": [{"tool": "place_trade", "input": {}}]}, "may not use"),
            ({**DATE_NIGHT, "steps": [{"tool": "update_setting", "input": {}}]}, "may not use"),
            ({**DATE_NIGHT, "steps": [{"tool": "run_skill", "input": {}}]}, "may not use"),
            ({**DATE_NIGHT, "params": []}, "undeclared"),
            ({**DATE_NIGHT, "name": "Date Night"}, "snake_case"),
            ({**DATE_NIGHT, "name": "play_music"}, "built-in"),
            ({**DATE_NIGHT, "steps": DATE_NIGHT["steps"] * 3}, "1-8 steps"),
        ):
            with self.assertRaises(ValueError) as ctx:
                skills.propose(**bad)
            self.assertIn(why, str(ctx.exception))

    def test_approving_answers_its_skill_request(self):
        request = skill_requests.file("Date night routine")
        record = skills.propose(**DATE_NIGHT, request_id=request["id"])
        skills.set_status(record["id"], "active")
        self.assertEqual(skill_requests.listing(), [])
        self.assertEqual(skill_requests.listing("done")[0]["id"], request["id"])

    def test_chat_proposal_opens_the_confirm_gate(self):
        reply = tools.execute_tool("propose_skill", {**DATE_NIGHT, "_jarvis_session_id": "s1"}, "s1")
        self.assertIn("Drafted skill", reply)
        [pending] = confirm_gate.pending("s1")
        self.assertIn("date_night", pending["description"])
        refused = tools.execute_tool("propose_skill", {**DATE_NIGHT, "steps": [{"tool": "place_trade"}]})
        self.assertIn("can't be a skill", refused)


def _response(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=None, model="m")


def _tool_use(name, args):
    return SimpleNamespace(type="tool_use", id="t1", name=name, input=args)


class DrafterTests(_Base):
    def test_drafts_a_skill_for_the_request(self):
        request = skill_requests.file("Date night", "set up date night with jazz")
        client = MagicMock()
        client.messages.create.side_effect = [
            _response(_tool_use("draft_skill", DATE_NIGHT), stop="tool_use"),
            _response(SimpleNamespace(type="text", text="Drafted date_night.")),
        ]
        with patch("brain.usage.record_response"):
            outcome = skill_drafter.draft(request["id"], client=client)
        self.assertIn("Drafted the date_night skill", outcome)
        [draft] = skills.listing("draft")
        self.assertEqual(draft["proposed_by"], "drafter")
        annotated = skill_requests.listing()[0]
        self.assertEqual(annotated["draft_skill_id"], draft["id"])
        system = client.messages.create.call_args_list[0].kwargs["system"]
        self.assertIn("play_music", system)
        self.assertNotIn("- place_trade:", system)

    def test_needs_more_leaves_a_note(self):
        request = skill_requests.file("Close the blinds")
        client = MagicMock()
        client.messages.create.side_effect = [
            _response(_tool_use("needs_more", {"note": "Needs the SwitchBot Blind Tilt and its HA integration."}), stop="tool_use"),
            _response(SimpleNamespace(type="text", text="Noted.")),
        ]
        with patch("brain.usage.record_response"):
            outcome = skill_drafter.draft(request["id"], client=client)
        self.assertIn("SwitchBot", outcome)
        self.assertIn("SwitchBot", skill_requests.listing()[0]["note"])
        self.assertEqual(skills.listing(), [])


class EndpointTests(_Base):
    def setUp(self):
        super().setUp()
        token = patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"})
        token.start()
        self.addCleanup(token.stop)
        server._runtime = MagicMock()
        self.addCleanup(setattr, server, "_runtime", None)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer test-token"}

    def test_list_approve_disable_delete(self):
        record = skills.propose(**DATE_NIGHT)
        [row] = self.client.get("/api/skills", headers=self.headers).json()["skills"]
        self.assertIn("play_music", row["summary"])
        for status in ("active", "disabled"):
            body = self.client.post(f"/api/skills/{record['id']}", json={"status": status}, headers=self.headers).json()
            self.assertEqual(body["skill"]["status"], status)
        self.client.post(f"/api/skills/{record['id']}", json={"status": "deleted"}, headers=self.headers)
        self.assertEqual(self.client.get("/api/skills", headers=self.headers).json()["skills"], [])
        self.assertEqual(self.client.post("/api/skills/nope", json={"status": "active"}, headers=self.headers).status_code, 404)

    def test_draft_starts_the_subagent(self):
        request = skill_requests.file("Date night")
        with patch.object(skill_drafter, "start", return_value=True) as start:
            body = self.client.post(f"/api/skills/requests/{request['id']}/draft", headers=self.headers).json()
        start.assert_called_once_with(request["id"])
        self.assertTrue(body["started"])
        self.assertTrue(skill_requests.listing()[0]["drafting"])
        self.assertEqual(self.client.post("/api/skills/requests/nope/draft", headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
