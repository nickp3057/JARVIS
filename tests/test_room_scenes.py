"""Scene tiles toggle, the room can be in no scene, and the fan ring is colour-only."""
import os
import unittest
from unittest import mock

from app import status_hub

_SCENES = [
    {"entity_id": "scene.game_time", "state": "2026-10-29T01:00:00", "attributes": {"id": "g", "friendly_name": "Game Time"}},
    {"entity_id": "scene.normal", "state": "2026-10-28T01:00:00", "attributes": {"id": "n", "friendly_name": "Normal"}},
]
_OK = "Command sent to Home Assistant."


class SceneToggleTests(unittest.TestCase):
    def setUp(self):
        status_hub.invalidate("scenes")

    def _states(self, lamp="on"):
        return _SCENES + [{"entity_id": "switch.lamp", "state": lamp, "attributes": {}}]

    def test_resting_scene_means_no_current(self):
        states = [dict(_SCENES[0], state="2026-10-27T01:00:00"), _SCENES[1]]
        with mock.patch.object(status_hub, "get_states", return_value=(True, states)):
            listing = status_hub._home_scenes_uncached()
        self.assertEqual(listing["last"], "scene.normal")
        self.assertIsNone(listing["current"])

    def _press(self, lamp):
        config = (True, {"entities": {"switch.lamp": {"state": "on"}}})
        with mock.patch.object(status_hub, "get_states", return_value=(True, self._states(lamp))), \
             mock.patch("integrations.home_assistant._request", return_value=config), \
             mock.patch.object(status_hub, "call_discovered_service", return_value=_OK) as call:
            result = status_hub.activate_scene("scene.game_time", toggle=True)
        return result, call.call_args.kwargs["entity_id"]

    def test_pressing_the_untouched_current_scene_leaves_it(self):
        result, ran = self._press("on")
        self.assertTrue(result["left"])
        self.assertEqual(ran, "scene.normal")

    def test_pressing_a_changed_current_scene_runs_it_again(self):
        result, ran = self._press("off")
        self.assertFalse(result["left"])
        self.assertEqual(ran, "scene.game_time")


class RingLightTests(unittest.TestCase):
    def test_ring_turns_on_in_colour_and_refuses_white(self):
        env = {"HOME_DASHBOARD_ENTITIES": "light.nicks_fan"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(status_hub, "call_discovered_service", return_value=_OK) as call:
            status_hub.home_control("light.nicks_fan", "on")
            self.assertIn("rgb_color", call.call_args.kwargs["service_data"])
            with self.assertRaises(ValueError):
                status_hub.set_light("light.nicks_fan", kelvin=3000)
        details = status_hub._light_details({"supported_color_modes": ["color_temp", "hs"]}, "light.nicks_fan")
        self.assertFalse(details["temperature"])


if __name__ == "__main__":
    unittest.main()
