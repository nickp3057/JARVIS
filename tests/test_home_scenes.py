"""The app's scene picker: saved HA scenes only, and only those can run."""
import unittest
from unittest.mock import patch

from app import status_hub

STATES = [
    {"entity_id": "scene.movie", "state": "2026-10-27T20:00:00+00:00",
     "attributes": {"id": "1", "friendly_name": "Movie", "icon": "mdi:movie"}},
    {"entity_id": "scene.good_morning", "state": "2026-10-28T14:00:00+00:00",
     "attributes": {"id": "2", "friendly_name": "Good Morning"}},
    {"entity_id": "scene.study", "state": "unknown", "attributes": {"id": "3", "friendly_name": "study"}},
    {"entity_id": "scene.barn_door_before", "state": "2026-10-28T05:28:18+00:00", "attributes": {}},
    {"entity_id": "light.desk", "state": "on", "attributes": {"id": "x"}},
]


class HomeScenesTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.invalidate("scenes")
        p = patch.object(status_hub, "get_states", return_value=(True, STATES))
        p.start()
        self.addCleanup(p.stop)

    def test_lists_saved_scenes_and_the_last_one_run(self) -> None:
        result = status_hub.home_scenes()
        self.assertEqual([s["entity_id"] for s in result["scenes"]],
                         ["scene.good_morning", "scene.movie", "scene.study"])
        self.assertEqual(result["last"], "scene.good_morning")
        self.assertIsNone(result["scenes"][2]["last_activated"])

    def test_only_saved_scenes_can_run(self) -> None:
        with patch.object(status_hub, "call_discovered_service", return_value="Turn On command sent to scene.movie.") as call:
            self.assertEqual(status_hub.activate_scene("scene.movie")["error"], "")
            call.assert_called_once_with("scene", "turn_on", entity_id="scene.movie")
            with self.assertRaises(ValueError):
                status_hub.activate_scene("scene.barn_door_before")


class LightControlTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.invalidate("entities")
        env = patch.dict("os.environ", {"HOME_DASHBOARD_ENTITIES": "light.desk,switch.lamp"})
        env.start()
        self.addCleanup(env.stop)

    def test_entities_carry_light_details_and_sun(self) -> None:
        states = [
            {"entity_id": "light.desk", "state": "on", "attributes": {
                "friendly_name": "Desk", "supported_color_modes": ["color_temp", "rgb"],
                "brightness": 128, "rgb_color": [255, 0, 0], "min_color_temp_kelvin": 2000}},
            {"entity_id": "switch.lamp", "state": "off", "attributes": {}},
            {"entity_id": "sun.sun", "state": "below_horizon", "attributes": {}},
        ]
        with patch.object(status_hub, "get_states", return_value=(True, states)):
            result = status_hub.home_entities()
        self.assertEqual(result["sun"], "below_horizon")
        light = result["entities"][0]["light"]
        self.assertEqual((light["dimmable"], light["color"], light["brightness_pct"], light["rgb"]),
                         (True, True, 50, [255, 0, 0]))
        self.assertNotIn("light", result["entities"][1])

    def test_set_light_only_allowlisted_lights(self) -> None:
        with patch.object(status_hub, "call_discovered_service", return_value="Turn On command sent.") as call:
            self.assertEqual(status_hub.set_light("light.desk", 40, kelvin=2700)["error"], "")
        call.assert_called_once_with("light", "turn_on", entity_id="light.desk",
                                     service_data={"brightness_pct": 40, "color_temp_kelvin": 2700})
        with patch.object(status_hub, "call_discovered_service", return_value="Turn On command sent.") as call, \
                patch.dict("os.environ", {"LIGHT_RAINBOWS": "light.desk=select.desk_diy_scene:New Rainbow"}):
            self.assertEqual(status_hub.set_light("light.desk", rainbow=True)["error"], "")
        self.assertEqual(call.call_args.args[:2], ("select", "select_option"))
        self.assertEqual(call.call_args.kwargs, {"entity_id": "select.desk_diy_scene",
                                                  "service_data": {"option": "New Rainbow"}})
        for bad in ("switch.lamp", "light.kitchen"):
            with self.assertRaises(ValueError):
                status_hub.set_light(bad, 50)


class SceneForceOffTests(unittest.TestCase):
    """HA's scene skips a light it already thinks is off; Govee's cloud
    integration can think so while the light is lit (bedtime 2026-11-01)."""

    CONFIG = {"entities": {"light.desk_lights_2": {"state": "off"}, "light.bedroom_lights": "off",
                           "light.nick_strip_lights": {"state": "on"}, "fan.nicks_fan": {"state": "off"}}}

    def test_off_lights_include_each_twin(self) -> None:
        self.assertEqual(status_hub.scene_off_lights(self.CONFIG),
                         ["light.bedroom_lights", "light.desk_lights", "light.desk_lights_2"])
        self.assertEqual(status_hub.scene_off_lights(None), [])

    def test_force_offs_sends_one_plain_turn_off(self) -> None:
        with patch("integrations.home_assistant._request", return_value=(True, self.CONFIG)) as req, \
                patch("integrations.home_assistant.call_service", return_value=(True, "")) as call:
            status_hub._force_scene_offs("scene.goodnight", delay=0)
        req.assert_called_once_with("GET", "/api/config/scene/config/goodnight")
        call.assert_called_once_with("light", "turn_off", service_data={
            "entity_id": ["light.bedroom_lights", "light.desk_lights", "light.desk_lights_2"]})
