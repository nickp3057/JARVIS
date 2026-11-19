"""Exit Movie: the room snapshotted when Movie runs, put back on exit, and
the app told the room left Movie (brain/movie_exit.py)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import status_hub
from brain import light_sync, movie_exit, protocols
from integrations import home_assistant

MOVIE_AT = "2026-11-19T02:00:00+00:00"
STATES = [
    {"entity_id": "scene.movie", "state": MOVIE_AT, "attributes": {"id": "1", "friendly_name": "Movie"}},
    {"entity_id": "scene.study", "state": "2026-11-19T01:00:00+00:00", "attributes": {"id": "2", "friendly_name": "Study"}},
]


class MovieExitTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.invalidate("scenes")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (
            patch.object(movie_exit, "_DIR", Path(tmp.name)),
            patch.object(movie_exit, "_SNAPSHOTS", True),
            patch.object(status_hub, "get_states", return_value=(True, STATES)),
            patch.dict("os.environ", {"HOME_DASHBOARD_ENTITIES": "light.desk,switch.wii_u,fan.nicks_fan,scene.movie"}),
            patch.object(protocols, "_save_effects"),
            patch.object(protocols, "_save_fan"),
            patch.object(protocols, "_effects_to_restore", return_value={}),
            patch.object(protocols, "_fan_back"),
            patch.object(light_sync, "release_for_movie_exit"),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.calls = []
        p = patch.object(home_assistant, "call_service",
                         side_effect=lambda *a, **k: self.calls.append((a, k)) or (True, "Command sent."))
        p.start()
        self.addCleanup(p.stop)

    def test_movie_snapshots_the_room_without_the_wii(self) -> None:
        with patch.object(movie_exit.time, "time", return_value=1_700_000_000):
            movie_exit.before_movie("scene.study")
        (args, kwargs), = self.calls
        self.assertEqual(args[:2], ("scene", "create"))
        self.assertEqual(kwargs["service_data"]["snapshot_entities"], ["light.desk", "fan.nicks_fan"])

    def test_rerunning_movie_keeps_the_first_snapshot(self) -> None:
        movie_exit.before_movie("scene.movie")
        self.assertEqual(self.calls, [])

    def test_exit_restores_the_snapshot_and_the_earlier_scene(self) -> None:
        movie_at = movie_exit._iso_seconds(MOVIE_AT)
        with patch.object(movie_exit.time, "time", return_value=movie_at - 1):
            movie_exit.before_movie("scene.study")
        self.assertEqual(status_hub.home_scenes()["current"], "scene.movie")
        result = movie_exit.exit_movie()
        self.assertEqual((result["error"], result["restored"], result["current"]), ("", True, "scene.study"))
        self.assertIn((("scene", "turn_on", "scene.movie_before"), {}), self.calls)
        light_sync.release_for_movie_exit.assert_called_once()
        self.assertEqual(status_hub.home_scenes()["current"], "scene.study")

    def test_a_stale_snapshot_falls_back_to_normal(self) -> None:
        movie_at = movie_exit._iso_seconds(MOVIE_AT)
        with patch.object(movie_exit.time, "time", return_value=movie_at - 3600):
            movie_exit.before_movie("scene.study")
        with patch.object(status_hub, "_leave_scene", return_value="Turn On command sent to scene.normal.") as leave:
            result = movie_exit.exit_movie()
        leave.assert_called_once_with("scene.movie")
        self.assertFalse(result["restored"])
        self.assertIsNone(status_hub.home_scenes()["current"])

    def test_not_in_movie_does_nothing(self) -> None:
        states = [dict(STATES[0], state="2026-11-18T00:00:00+00:00"), STATES[1]]
        with patch.object(status_hub, "get_states", return_value=(True, states)):
            status_hub.invalidate("scenes")
            result = movie_exit.exit_movie()
        self.assertFalse(result["restored"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
