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
