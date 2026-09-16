"""Self-diagnosis ledger: JARVIS notices, reports, and forgets its own tool failures."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import tool_health
from brain.personality import build_systems_health_section


class ToolHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.file_patch = patch.object(tool_health, "_FILE", Path(self.tmp.name) / "tool_health.json")
        self.file_patch.start()

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.tmp.cleanup()

    def test_healthy_ledger_renders_nothing(self) -> None:
        tool_health.record("get_time", ok=True, elapsed_ms=3)
        self.assertEqual(tool_health.render_block(), "")
        self.assertEqual(build_systems_health_section(tool_health.render_block()), "")
        self.assertIn("Nothing is degraded", tool_health.report())

    def test_one_failure_is_not_degraded_but_two_are(self) -> None:
        tool_health.record("spotify_play", ok=False, error="Tool error: Spotify not configured")
        self.assertEqual(tool_health.degraded_tools(), [])
        tool_health.record("spotify_play", ok=False, error="Tool error: Spotify not configured")
        names = [t["name"] for t in tool_health.degraded_tools()]
        self.assertEqual(names, ["spotify_play"])
        block = build_systems_health_section(tool_health.render_block())
        self.assertIn("Systems health", block)
        self.assertIn("spotify_play: 2 failures in a row", block)
        self.assertIn("Spotify not configured", block)
        self.assertIn("Degraded right now", tool_health.report())

    def test_success_recovers_and_clear_forgets(self) -> None:
        for _ in range(2):
            tool_health.record("control_device", ok=False, error="HA unreachable")
        tool_health.record("control_device", ok=True)
        self.assertEqual(tool_health.degraded_tools(), [])
        self.assertIn("Occasional faults", tool_health.report())
        for _ in range(2):
            tool_health.record("control_device", ok=False, error="HA unreachable")
        self.assertIn("Cleared the fault flag", tool_health.clear("control_device"))
        self.assertEqual(tool_health.degraded_tools(), [])
        self.assertIn("No health record", tool_health.clear("nope"))
        self.assertIn("ledger cleared", tool_health.clear())
        self.assertIn("No tool activity", tool_health.report())

    def test_scattered_failures_within_window_degrade(self) -> None:
        for _ in range(4):
            tool_health.record("get_weather", ok=False, error="timed out")
            tool_health.record("get_weather", ok=True)
        self.assertEqual([t["name"] for t in tool_health.degraded_tools()], ["get_weather"])

    def test_failure_heuristic_on_reply_text(self) -> None:
        self.assertTrue(tool_health.looks_like_failure("Tool error while running x: boom"))
        self.assertTrue(tool_health.looks_like_failure("I can't reach Home Assistant right now."))
        self.assertTrue(tool_health.looks_like_failure("Radarr is not configured, sir."))
        self.assertFalse(tool_health.looks_like_failure("The lights are on, sir."))
        self.assertFalse(tool_health.looks_like_failure(""))

    def test_execute_tool_scores_calls(self) -> None:
        from brain import tools

        with patch.dict(tools._DISPATCH, {"boom": lambda _a: (_ for _ in ()).throw(RuntimeError("kaput"))}):
            tools.execute_tool("boom", {})
            tools.execute_tool("boom", {})
        block = tool_health.render_block()
        self.assertIn("boom: 2 failures in a row", block)
        self.assertIn("RuntimeError: kaput", block)
        self.assertIn("Degraded right now", tools.execute_tool("system_health", {}))
        self.assertIn("Cleared the fault flag", tools.execute_tool("clear_tool_health", {"tool": "boom"}))
        self.assertEqual(tool_health.render_block(), "")


if __name__ == "__main__":
    unittest.main()
