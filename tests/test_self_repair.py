"""Governed self-repair: diagnosis picks a bounded fix, and nothing runs
without the exact id and Nick's go-ahead (stack fixes: Nick himself)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import self_repair, tool_health, confirm_gate

CMDS = "restart_home_assistant=docker restart homeassistant; restart_radarr = ssh nas 'docker restart radarr' ;bad"


class SelfRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(self_repair, "_FILE", Path(tmp.name) / "repairs.json").start()
        patch.object(tool_health, "degraded_tools", return_value=[{"name": "control_device", "last_error": "401"}]).start()
        self.cleared = patch.object(tool_health, "clear").start()
        patch.dict(os.environ, {"HOME_ASSISTANT_URL": "http://10.0.0.147:8123", "HOME_ASSISTANT_TOKEN": "t",
                                "SELF_REPAIR_COMMANDS": CMDS}).start()
        self.addCleanup(patch.stopall)

    def stage(self, tool: str = "control_device", reachable: bool = True) -> str:
        with patch.object(self_repair, "_probe_host", return_value=(reachable, "probe")):
            text = self_repair.diagnose(tool)
        return text.split("Proposed repair [")[1].split("]")[0]

    def pending(self) -> list[dict]:
        return [p for p in self_repair._load() if p["status"] == "pending"]

    def test_approved_commands_and_playbooks(self) -> None:
        self.assertEqual(self_repair.approved_commands(), {
            "restart_home_assistant": "docker restart homeassistant",
            "restart_radarr": "ssh nas 'docker restart radarr'"})
        self.assertEqual(self_repair.subsystem_for("Play_Music"), "spotify")
        self.assertIsNone(self_repair.subsystem_for("get_time"))
        self.assertIn("don't have a repair playbook", self_repair.diagnose("get_time"))

    def test_probe(self) -> None:
        with patch.object(self_repair, "_probe_host", return_value=(False, "10.0.0.147:8123 unreachable")) as host:
            result = self_repair.probe("home_assistant")
        host.assert_called_once_with("10.0.0.147", 8123)
        self.assertEqual((result["reachable"], result["missing_settings"]), (False, []))
        with patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}), \
                patch.object(self_repair, "_probe_host", return_value=(True, "ok")) as host:
            result = self_repair.probe("spotify")
        host.assert_called_once_with("api.spotify.com", 443)
        self.assertEqual(len(result["missing_settings"]), 2)

    def test_diagnosis_choices(self) -> None:
        self.stage(reachable=False)
        (proposal,) = self.pending()
        self.assertEqual(proposal["action"]["kind"], "run_command")
        self.assertEqual(proposal["action"]["name"], "restart_home_assistant")
        self.assertTrue(proposal["action"]["command_digest"])
        self.stage(reachable=True)  # a new diagnosis replaces the old pending one for that tool
        (proposal,) = self.pending()
        self.assertEqual(proposal["action"]["kind"], "clear_flag")
        with patch.dict(os.environ, {"HOME_ASSISTANT_TOKEN": ""}):
            self.stage()
        self.assertEqual(self.pending()[0]["action"]["kind"], "clear_flag")  # a secret: Nick adds it by hand
        with patch.dict(os.environ, {"SELF_REPAIR_COMMANDS": ""}):
            self.stage(reachable=False)
        self.assertIn("no approved restart command", self.pending()[0]["rationale"])

    def test_nothing_runs_without_the_go_ahead(self) -> None:
        pid = self.stage(reachable=False)
        self.assertIn("explicit go-ahead", self_repair.apply(pid))
        self.assertIn("No repair proposal", self_repair.apply("nope", confirmed=True))
        self.assertIn("discarded", self_repair.reject(pid))
        self.assertIn("already rejected", self_repair.apply(pid, confirmed=True))
        self.assertIn("No pending", self_repair.reject(pid))

    def test_run_command(self) -> None:
        pid = self.stage(reachable=False)
        done = subprocess.CompletedProcess([], 0, stdout="homeassistant\n", stderr="")
        with patch.object(self_repair.subprocess, "run", return_value=done) as run, \
                patch.object(self_repair.time, "sleep"), \
                patch.object(self_repair, "_probe_host", return_value=(True, "ok")):
            reply = self_repair.apply(pid, confirmed=True, approved_by="app")
        self.assertEqual(run.call_args.args[0], ["docker", "restart", "homeassistant"])
        self.assertIn("Repair applied", reply)
        self.assertIn("Endpoint is back.", reply)
        self.cleared.assert_called_once_with("control_device")

    def test_failed_command_is_reported_and_keeps_the_flag(self) -> None:
        pid = self.stage(reachable=False)
        with patch.object(self_repair.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="no such container")), \
                self.assertLogs("jarvis.brain.self_repair", level="WARNING"):
            reply = self_repair.apply(pid, confirmed=True, approved_by="app")
        self.assertIn("Repair failed", reply)
        self.assertIn("no such container", reply)
        self.cleared.assert_not_called()
        self.assertEqual(self_repair._load()[-1]["status"], "failed")

    def test_model_cannot_approve_a_command(self) -> None:
        pid = self.stage(reachable=False)
        with patch.object(self_repair.subprocess, "run") as run:
            self.assertIn("needs Nick's own approval", self_repair.apply(pid, confirmed=True))
        run.assert_not_called()
        self.assertEqual(self.pending()[0]["id"], pid)

    def test_changed_or_legacy_command_requires_fresh_approval(self) -> None:
        for legacy in (False, True):
            pid = self.stage(reachable=False)
            if legacy:
                items = self_repair._load()
                items[-1]["action"].pop("command_digest")
                self_repair._save(items)
            with patch.dict(os.environ, {"SELF_REPAIR_COMMANDS": "restart_home_assistant=echo changed"}), \
                    patch.object(self_repair.subprocess, "run") as run:
                reply = self_repair.apply(pid, confirmed=True, approved_by="app")
            self.assertIn("Diagnose and approve again", reply)
            run.assert_not_called()

    def test_chat_confirmation_is_bound_to_its_session(self) -> None:
        with patch.object(self_repair, "_probe_host", return_value=(False, "down")):
            self_repair.diagnose("control_device", session_id="command-review")
        with patch.object(self_repair, "apply", return_value="approved") as apply:
            self.assertIsNone(confirm_gate.conversation_action("yes", "other-review"))
            self.assertEqual(confirm_gate.conversation_action("yes", "command-review"), "approved")
        self.assertEqual(apply.call_args.kwargs, {"confirmed": True, "approved_by": "chat"})

    def test_set_setting_and_honest_rollback(self) -> None:
        proposal = self_repair._new_proposal("home_assistant", "control_device",
                                             {"kind": "set_setting", "key": "WAKE_THRESHOLD", "value": None}, "x")
        self_repair._save([proposal])
        self.assertIn("needs a value", self_repair.apply(proposal["id"], confirmed=True))
        with patch.object(self_repair.settings_mod, "update_setting",
                          side_effect=lambda k, v: f"{k} set to {v}.") as update, \
                patch.object(self_repair, "_probe_host", return_value=(False, "down")):
            with patch.dict(os.environ, {"WAKE_THRESHOLD": "0.5"}):
                reply = self_repair.apply(proposal["id"], confirmed=True, value="0.6")
        self.assertIn("rolled WAKE_THRESHOLD back", reply)
        self.assertEqual(update.call_args.args, ("WAKE_THRESHOLD", "0.5"))
        self.cleared.assert_not_called()

        proposal = self_repair._new_proposal("home_assistant", "control_device",
                                             {"kind": "set_setting", "key": "WAKE_THRESHOLD", "value": "0.6"}, "x")
        self_repair._save([proposal])
        with patch.object(self_repair.settings_mod, "update_setting",
                          side_effect=lambda k, v: f"{k} set to {v}.") as update, \
                patch.object(self_repair, "_probe_host", return_value=(False, "down")), \
                patch.dict(os.environ, {"WAKE_THRESHOLD": ""}):
            reply = self_repair.apply(proposal["id"], confirmed=True)
        self.assertIn("was unset before", reply)   # no false "rolled back"
        self.assertEqual(update.call_count, 1)
        self.assertEqual(self_repair._load()[-1]["status"], "applied")

    def test_stack_fixes_need_nick_himself(self) -> None:
        proposal = self_repair._new_proposal("nas", "x", {"kind": "stack_fix", "name": "retest_indexers"}, "x")
        self_repair._save([proposal])
        self.assertIn("needs Nick's own approval", self_repair.apply(proposal["id"], confirmed=True))
        notes = []
        self_repair.set_notifier(lambda title, detail, severity, **_: notes.append((title, severity)))
        self.addCleanup(self_repair.set_notifier, None)

        class Inline:
            def __init__(self, target, args, daemon, name):  # noqa: ANN001
                self.target, self.args = target, args

            def start(self) -> None:
                self.target(*self.args)

        with patch.object(self_repair, "Thread", Inline), \
                patch("brain.stack_fixes.run", return_value=(True, "1337x passed its test.")), \
                patch("brain.stack_fixes.describe", return_value="Re-test indexers: ..."):
            reply = self_repair.apply(proposal["id"], confirmed=True, approved_by="app")
        self.assertIn("is running", reply)
        self.assertEqual(self_repair._load()[-1]["status"], "applied")
        self.assertEqual(notes, [("Repair done: Re-test indexers", "info")])

    def test_corrupt_store_is_empty_not_fatal(self) -> None:
        self_repair._FILE.write_text("{nope")
        with self.assertLogs("jarvis.brain.self_repair", level="WARNING"):
            self.assertEqual(self_repair._load(), [])


if __name__ == "__main__":
    unittest.main()
