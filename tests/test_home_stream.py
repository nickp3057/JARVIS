"""The apps' live room feed: HA changes bump status_hub's home counter."""
import os
import unittest
from unittest import mock

from app import status_hub
from brain import runtime


class HomeStreamTests(unittest.TestCase):
    def test_dashboard_entity_change_bumps_and_clears_entities(self):
        status_hub._cache["entities"] = (0.0, {"stale": True})
        before = status_hub.home_seq()
        with mock.patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": "light.lamp"}):
            runtime._invalidate_app_home_cache("light.lamp")
        self.assertEqual(status_hub.home_seq(), before + 1)
        self.assertNotIn("entities", status_hub._cache)

    def test_scene_run_clears_scenes(self):
        status_hub._cache["scenes"] = (0.0, {"stale": True})
        before = status_hub.home_seq()
        runtime._invalidate_app_home_cache("scene.movie")
        self.assertEqual(status_hub.home_seq(), before + 1)
        self.assertNotIn("scenes", status_hub._cache)

    def test_entity_off_the_dashboard_is_ignored(self):
        before = status_hub.home_seq()
        with mock.patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": "light.lamp"}):
            runtime._invalidate_app_home_cache("sensor.cpu")
        self.assertEqual(status_hub.home_seq(), before)


if __name__ == "__main__":
    unittest.main()
