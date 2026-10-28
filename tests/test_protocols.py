"""Barn Door Protocol: red room, red theme, and everything back after."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import event_bus, protocols, theme, tools


class BarnDoorTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(theme, "_FILE", Path(tmp.name) / "theme.json")
        p.start()
        self.addCleanup(p.stop)
        ha = patch.object(protocols.home_assistant, "call_service", return_value=(True, "ok"))
        self.ha = ha.start()
        self.addCleanup(ha.stop)
        event_bus.reset_for_tests()

    def calls(self, domain: str, service: str) -> list:
        return [c for c in self.ha.call_args_list if c.args[:2] == (domain, service)]

    def test_engage_and_stand_down(self) -> None:
        theme.choose("mark85")
        self.assertIn("engaged", tools.execute_tool("barn_door_protocol", {}))
        snapshot = self.calls("scene", "create")[0].kwargs["service_data"]
        self.assertIn("light.nicks_fan", snapshot["snapshot_entities"])
        red = self.calls("light", "turn_on")
        self.assertEqual(len(red), 5)
        self.assertEqual(red[0].args[3]["rgb_color"], [255, 0, 0])
        self.assertEqual(self.calls("light", "turn_off")[0].args[2], "light.bedroom_lights")
        self.assertEqual(theme.current()["theme"], "barnDoor")
        self.assertEqual(event_bus.since(0)[-1].severity, "critical")
        # Saying it twice must not snapshot the red room over the real one.
        protocols.engage()
        self.assertEqual(len(self.calls("scene", "create")), 1)
        self.assertIn("mark85", tools.execute_tool("barn_door_protocol", {"action": "stand_down"}))
        self.assertEqual(self.calls("scene", "turn_on")[0].args[2], "scene.barn_door_before")
        self.assertEqual(theme.current()["theme"], "mark85")


if __name__ == "__main__":
    unittest.main()
