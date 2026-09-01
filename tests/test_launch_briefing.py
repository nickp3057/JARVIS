"""Launch briefing delivery contract shared by every front end."""

from __future__ import annotations

import os
from threading import RLock
import unittest
from unittest.mock import Mock, patch

from brain.runtime import JarvisRuntime


class LaunchBriefingTests(unittest.TestCase):
    @staticmethod
    def _runtime() -> JarvisRuntime:
        runtime = object.__new__(JarvisRuntime)
        runtime._lock = RLock()
        runtime._launch_briefing_claimed = False
        runtime.compose_briefing = Mock(return_value="Good morning, sir.")
        return runtime

    def test_disabled_briefing_does_not_consume_future_delivery(self) -> None:
        runtime = self._runtime()
        with patch.dict(os.environ, {"BRIEFING_ON_LAUNCH": "false"}, clear=False):
            self.assertEqual(runtime.launch_briefing(), "")
        with patch.dict(os.environ, {"BRIEFING_ON_LAUNCH": "true"}, clear=False):
            self.assertEqual(runtime.launch_briefing(), "Good morning, sir.")

    def test_enabled_briefing_is_delivered_only_once(self) -> None:
        runtime = self._runtime()
        with patch.dict(os.environ, {"BRIEFING_ON_LAUNCH": "true"}, clear=False):
            self.assertEqual(runtime.launch_briefing(), "Good morning, sir.")
            self.assertEqual(runtime.launch_briefing(), "")
        runtime.compose_briefing.assert_called_once_with()

    def test_failed_composition_can_be_retried(self) -> None:
        runtime = self._runtime()
        runtime.compose_briefing = Mock(side_effect=[RuntimeError("offline"), "Recovered."])
        with patch.dict(os.environ, {"BRIEFING_ON_LAUNCH": "true"}, clear=False):
            self.assertEqual(runtime.launch_briefing(), "")
            self.assertEqual(runtime.launch_briefing(), "Recovered.")


if __name__ == "__main__":
    unittest.main()
