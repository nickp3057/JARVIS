"""'What scenes do I have?' and 'save this as a scene': capture, gate, write."""
import os
import unittest
from unittest.mock import patch

from app import status_hub
from brain import confirm_gate, tools

ROOM = [
    {"entity_id": "light.bedroom_lights", "state": "on",
     "attributes": {"brightness": 166, "color_mode": "color_temp", "color_temp_kelvin": 3000}},
    {"entity_id": "light.desk_lights_2", "state": "on",
     "attributes": {"brightness": 255, "color_mode": "rgb", "rgb_color": [255, 0, 128]}},
    {"entity_id": "light.nicks_fan", "state": "on",
     "attributes": {"brightness": 200, "color_mode": "color_temp", "color_temp_kelvin": 4000}},
    {"entity_id": "light.lamp", "state": "off", "attributes": {}},
    {"entity_id": "light.offline", "state": "unavailable", "attributes": {}},
    {"entity_id": "switch.wii_u", "state": "on", "attributes": {}},
    {"entity_id": "switch.tower", "state": "on", "attributes": {}},
    {"entity_id": "fan.nicks_fan", "state": "on", "attributes": {"percentage": 35}},
    {"entity_id": "light.not_allowlisted", "state": "on", "attributes": {}},
    {"entity_id": "scene.movie", "state": "2026-10-27T20:00:00+00:00",
     "attributes": {"id": "movie", "friendly_name": "Movie"}},
]
ALLOW = ("light.bedroom_lights,light.desk_lights_2,light.nicks_fan,light.lamp,light.offline,"
         "switch.wii_u,switch.tower,fan.nicks_fan,scene.movie")


class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.clear_cache()
        for p in (patch.object(status_hub, "get_states", return_value=(True, ROOM)),
                  patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": ALLOW, "RING_LIGHTS": "light.nicks_fan"})):
            p.start()
            self.addCleanup(p.stop)

    def test_captures_the_room_by_nicks_rules(self) -> None:
        entities = status_hub.capture_scene()
        self.assertEqual(entities["light.bedroom_lights"],
                         {"state": "on", "brightness": 166, "color_temp_kelvin": 3000})
        self.assertEqual(entities["light.desk_lights_2"]["rgb_color"], [255, 0, 128])
        self.assertEqual(entities["light.nicks_fan"], {"state": "off"})  # never saved on
        self.assertNotIn("switch.wii_u", entities)
        self.assertNotIn("light.offline", entities)
        self.assertNotIn("light.not_allowlisted", entities)
        self.assertEqual(entities["fan.nicks_fan"],
                         {"state": "on", "percentage": 35, "direction": "forward", "preset_mode": "fresh"})
        self.assertEqual(status_hub.describe_capture(entities), "2 lights on, 2 off, 1 plug on, fan at 35%")

    def test_save_posts_to_the_scene_config_api(self) -> None:
        with patch("integrations.home_assistant._request", return_value=(True, {"result": "ok"})) as request:
            result = status_hub.save_scene("Late Night Reading", {"light.lamp": {"state": "on"}})
        self.assertEqual(result, {"error": "", "entity_id": "scene.late_night_reading"})
        request.assert_called_once_with(
            "POST", "/api/config/scene/config/late_night_reading",
            json={"id": "late_night_reading", "name": "Late Night Reading",
                  "entities": {"light.lamp": {"state": "on"}}})


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.clear_cache()
        confirm_gate.clear_for_tests()
        for p in (patch.object(status_hub, "get_states", return_value=(True, ROOM)),
                  patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": ALLOW})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(confirm_gate.clear_for_tests)

    def test_list_scenes(self) -> None:
        self.assertIn("Saved scenes: Movie.", tools.execute_tool("list_scenes", {}))

    def test_save_only_proposes_until_nick_approves(self) -> None:
        with patch("integrations.home_assistant._request", return_value=(True, {})) as request:
            reply = tools.execute_tool("save_scene", {"name": "Movie", "_jarvis_session_id": "s1"})
            self.assertIn("replace the scene 'Movie'", reply)
            request.assert_not_called()
            done = confirm_gate.conversation_action("yes", "s1")
        self.assertIn("scene.movie", done)
        request.assert_called_once()

    def test_save_needs_a_name(self) -> None:
        self.assertIn("needs a name", tools.execute_tool("save_scene", {"name": "  ", "_jarvis_session_id": "s1"}))


if __name__ == "__main__":
    unittest.main()
