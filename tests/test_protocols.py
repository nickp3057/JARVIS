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
        effects = patch.object(protocols, "_EFFECTS_FILE", Path(tmp.name) / "effects.json")
        effects.start()
        self.addCleanup(effects.stop)
        fan = patch.object(protocols, "_FAN_FILE", Path(tmp.name) / "fan.json")
        fan.start()
        self.addCleanup(fan.stop)
        states = {"light.desk_lights_2": {"attributes": {"effect": "Rainbow"}},
                  "light.nick_strip_lights": {"state": "on", "attributes": {}},
                  "select.desk_lights_diy_scene": {"state": "New Rainbow", "attributes": {}},
                  "select.nick_strip_lights_diy_scene": {"state": "None", "attributes": {}}}
        get = patch.object(protocols.home_assistant, "_request",
                           side_effect=lambda _m, path: (True, states.get(path.rsplit("/", 1)[-1], {"attributes": {}})))
        get.start()
        self.addCleanup(get.stop)
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
        self.assertIn("light.lamp", [c.args[2] for c in red])  # the Govee lamp goes red too
        self.assertNotIn("light.nicks_fan", [c.args[2] for c in red])  # the fan light is never turned on
        self.assertIn("light.nicks_fan", [c.args[2] for c in self.calls("light", "turn_off")])
        self.assertEqual(len(self.calls("remote", "turn_on")), 1)  # the TV wakes for the red-alert screen
        self.assertEqual(red[0].args[3]["rgb_color"], [255, 0, 0])
        self.assertEqual(self.calls("light", "turn_off")[0].args[2], "light.bedroom_lights")
        self.assertEqual(self.calls("switch", "turn_off")[0].args[2], "switch.avengers_tower_socket_1")
        self.assertIn("switch.avengers_tower_socket_1", snapshot["snapshot_entities"])  # comes back on
        self.assertEqual(theme.current()["theme"], "barnDoor")
        self.assertEqual(event_bus.since(0), [])  # no alert banner or Systems line
        # The ring's follow-the-strip automation is paused, or red would make it rainbow.
        self.assertEqual(self.calls("automation", "turn_off")[0].args[2], "automation.fan_light_follows_strip")
        # Saying it twice must not snapshot the red room over the real one.
        protocols.engage()
        self.assertEqual(len(self.calls("scene", "create")), 1)
        self.assertIn("mark85", tools.execute_tool("barn_door_protocol", {"action": "stand_down"}))
        self.assertEqual(self.calls("scene", "turn_on")[0].args[2], "scene.barn_door_before")
        self.assertEqual(theme.current()["theme"], "mark85")
        # The strip was on, so the ring's rainbow comes back and it follows the strip again.
        self.assertEqual(self.calls("scene", "turn_on")[-1].args[2], "scene.fan_light_rainbow")
        self.assertEqual(len(self.calls("automation", "turn_on")), 1)
        # Effects come back too: the one HA saw, and the configured Govee one.
        restored = {c.args[2]: c.args[3]["effect"] for c in self.calls("light", "turn_on") if "effect" in c.args[3]}
        self.assertEqual(restored, {"light.desk_lights_2": "Rainbow"})

    def test_configured_effects_cover_what_ha_cannot_see(self) -> None:
        with patch.dict("os.environ", {"BARN_DOOR_EFFECTS": "light.nick_strip_lights=Rainbow-A"}):
            protocols.engage()
            protocols.stand_down()
        restored = {c.args[2]: c.args[3]["effect"] for c in self.calls("light", "turn_on") if "effect" in c.args[3]}
        self.assertEqual(restored, {"light.nick_strip_lights": "Rainbow-A", "light.desk_lights_2": "Rainbow"})

    def test_govee_diy_scenes_come_back_through_their_dropdowns(self) -> None:
        with patch.dict("os.environ", {"BARN_DOOR_EFFECTS":
                                       "select.nick_strip_lights_diy_scene=Real Rainbow,"
                                       "select.desk_lights_diy_scene=Real Rainbow"}):
            protocols.engage()
            protocols.stand_down()
        picked = {c.args[2]: c.args[3]["option"] for c in self.calls("select", "select_option")}
        # The strip showed no DIY scene at engage: the configured one. The desk
        # had New Rainbow running: that wins.
        self.assertEqual(picked, {"select.nick_strip_lights_diy_scene": "Real Rainbow",
                                  "select.desk_lights_diy_scene": "New Rainbow"})


if __name__ == "__main__":
    unittest.main()
