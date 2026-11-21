import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server
from app import status_hub
from brain import presence
from brain.presence import PresenceWatch


def _states(person_state):
    return True, [{"entity_id": "person.nick", "state": person_state}, {"entity_id": "light.x", "state": "on"}]


class LocationTests(unittest.TestCase):
    def setUp(self):
        presence.reset_for_tests()
        self.addCleanup(presence.reset_for_tests)

    def test_reads_the_person_entity(self):
        for state, expected in (("home", "home"), ("not_home", "away"), ("Work", "away"),
                                ("unavailable", None), ("unknown", None)):
            with patch("integrations.home_assistant.get_states", return_value=_states(state)):
                self.assertEqual(presence.location(fresh=True), expected, state)
        with patch("integrations.home_assistant.get_states", return_value=(False, "HA down")):
            self.assertIsNone(presence.location(fresh=True))

    def test_switch_off_and_cache(self):
        with patch.dict(os.environ, {"PRESENCE_ENABLED": "false"}):
            self.assertIsNone(presence.location())
        with patch("integrations.home_assistant.get_states", return_value=_states("home")) as get:
            presence.location(fresh=True)
            presence.location()
        self.assertEqual(get.call_count, 1)


class RoomAwayTests(unittest.TestCase):
    def test_gps_decides_whatever_the_scene(self):
        with patch.object(presence, "location", return_value="away"), \
             patch.object(status_hub, "home_scenes", return_value={"error": "", "last": "scene.movie"}):
            self.assertTrue(status_hub.room_away())
        with patch.object(presence, "location", return_value="home"), \
             patch.object(status_hub, "home_scenes", return_value={"error": "", "last": "scene.peace"}):
            self.assertFalse(status_hub.room_away())

    def test_unknown_gps_falls_back_to_peace_and_lights_off(self):
        entities = [{"entity_id": "light.a", "domain": "light", "state": "off"}]
        with patch.object(presence, "location", return_value=None), \
             patch.object(status_hub, "home_scenes", return_value={"error": "", "last": "scene.peace"}), \
             patch.object(status_hub, "home_entities", return_value={"error": "", "entities": entities}):
            self.assertTrue(status_hub.room_away())


class WatchTests(unittest.TestCase):
    def test_transitions_need_to_hold_and_jitter_is_ignored(self):
        clock = [0.0]
        watch = PresenceWatch(lambda: clock[0])
        with patch.dict(os.environ, {"PRESENCE_CONFIRM_S": "180"}):
            self.assertIsNone(watch.step("home"))  # the first reading sets the baseline
            clock[0] = 60
            self.assertIsNone(watch.step("away"))
            clock[0] = 120
            self.assertIsNone(watch.step("home"))  # a GPS blip back
            clock[0] = 200
            self.assertIsNone(watch.step("away"))
            clock[0] = 300
            self.assertIsNone(watch.step(None))  # unknown says nothing
            clock[0] = 390
            change = watch.step("away")
            self.assertEqual((change.kind, change.since), ("left", 200))
            clock[0] = 400
            self.assertIsNone(watch.step("home"))
            clock[0] = 600
            self.assertEqual(watch.step("home").kind, "arrived")


class RuntimeTests(unittest.TestCase):
    def _runtime(self):
        from brain.runtime import JarvisRuntime
        rt = JarvisRuntime.__new__(JarvisRuntime)
        rt.record_event = MagicMock()
        rt.raise_alert = MagicMock()
        return rt

    def test_leaving_with_lights_on_offers_to_turn_them_off(self):
        rt = self._runtime()
        lights = [{"entity_id": "light.desk", "name": "Desk lights"}, {"entity_id": "light.lamp", "name": "Lamp"}]
        with patch.object(presence, "lights_left_on", return_value=lights):
            rt._presence_changed(presence.Change("left", 0))
        args, kwargs = rt.raise_alert.call_args
        self.assertEqual(args[2], "Desk lights and Lamp are still on in your room.")
        self.assertEqual((kwargs["category"], kwargs["severity"]), ("LEFT_ON", "info"))

    def test_leaving_with_everything_off_or_alert_switched_off_is_quiet(self):
        rt = self._runtime()
        with patch.object(presence, "lights_left_on", return_value=[]):
            rt._presence_changed(presence.Change("left", 0))
        with patch.dict(os.environ, {"PRESENCE_LEFT_ON_ALERT": "0"}), \
             patch.object(presence, "lights_left_on", return_value=[{"entity_id": "light.a", "name": "A"}]):
            rt._presence_changed(presence.Change("left", 0))
        rt.raise_alert.assert_not_called()
        rt._presence_changed(presence.Change("arrived", 0))
        self.assertEqual(rt.record_event.call_args.kwargs["title"], "Nick is home")


class EndpointTests(unittest.TestCase):
    def test_lights_off_button(self):
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"}):
            server._runtime = MagicMock()
            self.addCleanup(setattr, server, "_runtime", None)
            client = TestClient(server.app)
            self.addCleanup(client.close)
            lights = [{"entity_id": "light.desk", "name": "Desk"}, {"entity_id": "light.lamp", "name": "Lamp"}]
            with patch.object(presence, "lights_left_on", return_value=lights), \
                 patch("integrations.home_assistant.call_service", side_effect=[(True, ""), (False, "no")]) as call:
                body = client.post("/api/presence/lights-off", headers={"Authorization": "Bearer test-token"}).json()
        self.assertEqual(call.call_args_list[0].args, ("light", "turn_off", "light.desk"))
        self.assertEqual(body["message"], "Turned off 1 light. Couldn't turn off Lamp.")


if __name__ == "__main__":
    unittest.main()
