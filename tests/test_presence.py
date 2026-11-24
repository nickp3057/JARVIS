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


class PhoneSightingTests(unittest.TestCase):
    """The iPhone reaching Core through Lightsail means away even while HA,
    unreachable from outside, still says home (2026-11-24)."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        presence.reset_for_tests()
        self.addCleanup(presence.reset_for_tests)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        file_patch = patch.object(presence, "_PHONE_FILE", Path(tmp.name) / "presence.json")
        file_patch.start()
        self.addCleanup(file_patch.stop)

    @staticmethod
    def _ha(state, updated):
        return True, [{"entity_id": "person.nick", "state": state, "last_updated": updated}]

    def test_classify(self):
        for ip, expected in (("10.0.0.20", "home"), ("192.168.1.4", "home"), ("172.58.10.2", "away"),
                             ("8.8.8.8", "away"), ("100.69.8.11", None), ("127.0.0.1", None),
                             ("testclient", None), (None, None)):
            self.assertEqual(presence.classify(ip), expected, ip)
        with patch.dict(os.environ, {"PRESENCE_HOME_IPS": "73.1.2.3"}):
            self.assertEqual(presence.classify("73.1.2.3"), "home")

    def test_newer_signal_wins(self):
        morning = "2026-11-24T14:26:39+00:00"  # HA's last report, at home
        at_school = presence._parse_time("2026-11-24T15:30:00+00:00")
        presence.note_phone("172.58.10.2", now=at_school)
        with patch("integrations.home_assistant.get_states", return_value=self._ha("home", morning)):
            self.assertEqual(presence.location(fresh=True), "away")
        evening = "2026-11-25T00:10:00+00:00"  # back on Wi-Fi, the HA app reports again
        with patch("integrations.home_assistant.get_states", return_value=self._ha("home", evening)):
            self.assertEqual(presence.location(fresh=True), "home")

    def test_tailnet_says_nothing_and_ha_down_leaves_the_phone(self):
        presence.note_phone("100.119.21.113", now=10**10)
        with patch("integrations.home_assistant.get_states", return_value=self._ha("home", "2026-11-24T14:00:00+00:00")):
            self.assertEqual(presence.location(fresh=True), "home")
        presence.note_phone("8.8.8.8", now=1.0)
        with patch("integrations.home_assistant.get_states", return_value=(False, "HA down")):
            self.assertEqual(presence.location(fresh=True), "away")

    def test_sighting_survives_a_restart(self):
        presence.note_phone("8.8.8.8", now=1234.0)
        presence._phone.update(loaded=False, where=None, at=0.0)
        self.assertEqual(presence.phone_sighting(), ("away", 1234.0))

    def test_server_records_only_the_iphone_on_the_admin_token(self):
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"}), \
             patch.object(presence, "note_phone") as note:
            client = TestClient(server.app, client=("172.58.10.2", 5000))
            self.addCleanup(client.close)
            with patch("integrations.home_assistant.get_states", return_value=(False, "HA down")):
                for device, token in (("iphone", "test-token"), ("ipad", "test-token"), ("iphone", "wrong")):
                    client.get("/api/presence", headers={"Authorization": f"Bearer {token}",
                                                         "X-Jarvis-Device": device})
        note.assert_called_once_with("172.58.10.2")


    def test_geofence_report_and_zone_routes(self):
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"}):
            client = TestClient(server.app)
            self.addCleanup(client.close)
            auth = {"Authorization": "Bearer test-token"}
            states = (True, [{"entity_id": "person.nick", "state": "home", "last_updated": "2026-11-24T14:00:00+00:00"},
                             {"entity_id": "zone.home", "state": "1",
                              "attributes": {"latitude": 40.5, "longitude": -111.9, "radius": 22}}])
            with patch("integrations.home_assistant.get_states", return_value=states):
                body = client.post("/api/presence/phone", json={"where": "away"}, headers=auth).json()
                self.assertEqual(body["location"], "away")
                self.assertEqual(client.post("/api/presence/phone", json={"where": "mars"}, headers=auth).status_code, 422)
                zone = client.get("/api/presence/zone", headers=auth).json()["zone"]
        self.assertEqual(zone, {"latitude": 40.5, "longitude": -111.9, "radius": 22.0})


if __name__ == "__main__":
    unittest.main()
