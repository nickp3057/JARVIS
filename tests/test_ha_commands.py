"""Spoken home commands matched to Home Assistant entities: which device,
which action, and the guard rails on brightness, colour and temperature."""
import unittest
from unittest.mock import patch

from integrations import home_assistant as ha

STATES = [
    {"entity_id": "light.desk_lights", "state": "off", "attributes": {}},
    {"entity_id": "light.bedroom_lights", "state": "off", "attributes": {}},
    {"entity_id": "switch.lamp", "state": "off", "attributes": {}},
    {"entity_id": "fan.nicks_fan", "state": "off", "attributes": {}},
    {"entity_id": "scene.game_time", "state": "x", "attributes": {}},
    {"entity_id": "climate.hallway", "state": "heat", "attributes": {"temperature_unit": "°C"}},
]


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.states = patch.object(ha, "get_states", return_value=(True, STATES)).start()
        self.call = patch.object(ha, "call_service", return_value=(True, "Turn On command sent.")).start()
        self.addCleanup(patch.stopall)

    def test_action_words(self) -> None:
        for said, action in (("turn off the desk lights", "turn_off"), ("Switch on the lamp", "turn_on"),
                             ("desk lights off", "turn_off"), ("the light on my desk, turn off", "turn_off"),
                             ("make it nice", None)):
            self.assertEqual(ha._extract_action(said), action, said)

    def test_on_off_picks_the_device_in_one_state_read(self) -> None:
        self.assertEqual(ha.handle_device_command("turn on the lamp"), "Turn On command sent.")
        self.call.assert_called_with("switch", "turn_on", "switch.lamp")
        self.assertEqual(self.states.call_count, 1)  # switch + light + fan from one read
        ha.handle_device_command("turn off the fan")
        self.call.assert_called_with("fan", "turn_off", "fan.nicks_fan")
        ha.handle_device_command("desk lights off")
        self.call.assert_called_with("light", "turn_off", "light.desk_lights")

    def test_unclear_requests_ask_instead_of_guessing(self) -> None:
        self.assertIn("on or off", ha.handle_device_command("do the lamp"))
        self.assertIn("multiple possible matches", ha.handle_device_command("turn on the lights"))
        self.assertIn("could not find", ha.handle_device_command("turn on the toaster"))
        self.call.assert_not_called()

    def test_brightness(self) -> None:
        ha.set_light_brightness("bedroom", 40)
        self.call.assert_called_with("light", "turn_on", "light.bedroom_lights", {"brightness_pct": 40})
        ha.set_light_brightness("bedroom", 0)
        self.call.assert_called_with("light", "turn_off", "light.bedroom_lights", {})
        self.call.reset_mock()
        for bad in (101, -1, 40.5, True):
            self.assertTrue(ha.set_light_brightness("bedroom", bad).startswith("Brightness must"), bad)
        self.call.assert_not_called()
        self.assertIn("name of the light", ha.set_light_brightness("", 40))

    def test_colours_are_an_allowlist(self) -> None:
        ha.set_light_color("desk", "Purple")
        self.call.assert_called_with("light", "turn_on", "light.desk_lights", {"color_name": "purple"})
        self.call.reset_mock()
        self.assertIn("safe color list", ha.set_light_color("desk", "infrared"))
        self.call.assert_not_called()

    def test_thermostat_converts_units_and_refuses_extremes(self) -> None:
        ha.set_climate_temperature("hallway", 68, "°F")
        self.call.assert_called_with("climate", "set_temperature", "climate.hallway", {"temperature": 20.0})
        self.call.reset_mock()
        for value, unit in ((100, "F"), (float("nan"), "F"), (20, "K"), ("hot", "F")):
            ha.set_climate_temperature("hallway", value, unit)
        self.call.assert_not_called()

    def test_scenes_scripts_automations(self) -> None:
        ha.activate_home_action("scenes", "game time")
        self.call.assert_called_with("scene", "turn_on", "scene.game_time")
        self.assertIn("scenes, scripts, or automations", ha.activate_home_action("timer", "x"))
        self.assertIn("name of the scene", ha.activate_home_action("scene", " "))

    def test_ha_down(self) -> None:
        self.states.return_value = (False, "Could not reach Home Assistant")
        self.assertIn("Could not reach", ha.handle_device_command("turn on the lamp"))


if __name__ == "__main__":
    unittest.main()
