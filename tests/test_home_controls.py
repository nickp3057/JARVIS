"""Home controls shared by the apps and the room voice: the HA service
cache, scenes, light settings, the new endpoints and client-token scopes."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import client_tokens, server, status_hub
from integrations import home_assistant as ha

SERVICES = [{"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
            {"domain": "scene", "services": {"turn_on": {}}}]


class ServiceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        ha.clear_services_cache()
        self.addCleanup(ha.clear_services_cache)

    def test_catalogue_is_read_once_then_cached(self) -> None:
        with patch.object(ha, "_request", return_value=(True, SERVICES)) as request:
            self.assertEqual(ha.list_services("light"), (True, ["light.turn_off", "light.turn_on"]))
            ha.list_services("scene")
            ha.get_services()
        self.assertEqual(request.call_count, 1)

    def test_fresh_and_failures(self) -> None:
        with patch.object(ha, "_request", return_value=(False, "offline")) as request:
            ha.get_services()
            ha.get_services()
        self.assertEqual(request.call_count, 2)  # a failure is never cached
        with patch.object(ha, "_request", return_value=(True, SERVICES)) as request:
            ha.get_services()
            ha.get_services(fresh=True)
        self.assertEqual(request.call_count, 2)

    def test_a_service_added_since_the_cache_is_found(self) -> None:
        newer = SERVICES + [{"domain": "select", "services": {"select_option": {}}}]
        replies = iter([(True, SERVICES), (True, newer), (True, {"ok": True})])
        with patch.object(ha, "_request", side_effect=lambda *a, **k: next(replies)):
            ha.get_services()  # cache the older catalogue
            message = ha.call_discovered_service("select", "select_option", entity_id="select.x",
                                                 service_data={"option": "A"})
        self.assertIn("command sent", message.lower())

    def test_unknown_service_still_refused(self) -> None:
        with patch.object(ha, "_request", return_value=(True, SERVICES)):
            message = ha.call_discovered_service("light", "explode", entity_id="light.x")
        self.assertIn("does not advertise", message)


class SceneAndLightTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.invalidate("scenes")
        status_hub.invalidate("entities")
        env = patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": "light.desk,light.strip,switch.lamp",
                                      "LIGHT_RAINBOWS": ""})
        env.start()
        self.addCleanup(env.stop)
        self.call = patch.object(status_hub, "call_discovered_service",
                                 return_value="Turn On command sent.").start()
        self.addCleanup(patch.stopall)

    def test_activate_finds_a_scene_made_after_the_list_was_cached(self) -> None:
        old = [{"entity_id": "scene.movie", "state": "x", "attributes": {"id": "1", "friendly_name": "Movie"}}]
        new = old + [{"entity_id": "scene.peace", "state": "x", "attributes": {"id": "2", "friendly_name": "Peace"}}]
        with patch.object(status_hub, "get_states", side_effect=[(True, old), (True, new)]):
            status_hub.home_scenes()  # cached without Peace
            self.assertEqual(status_hub.activate_scene("scene.peace")["error"], "")
        self.call.assert_called_once_with("scene", "turn_on", entity_id="scene.peace")

    def test_set_light_clamps_and_prefers_colour_over_white(self) -> None:
        status_hub.set_light("light.desk", 0, rgb=[300, -5, 128], kelvin=2700)
        self.assertEqual(self.call.call_args.kwargs["service_data"],
                         {"brightness_pct": 1, "rgb_color": [255, 0, 128]})
        status_hub.set_light("light.desk", 250, kelvin=100)
        self.assertEqual(self.call.call_args.kwargs["service_data"],
                         {"brightness_pct": 100, "color_temp_kelvin": 1500})

    def test_rainbows_from_settings(self) -> None:
        with patch.dict(os.environ, {"LIGHT_RAINBOWS": "light.strip=select.strip_diy:Real Rainbow, bad, "
                                                       "light.x=switch.y:Nope"}):
            self.assertEqual(status_hub.rainbows(), {"light.strip": ("select.strip_diy", "Real Rainbow")})
            with self.assertRaises(ValueError):
                status_hub.set_light("light.desk", rainbow=True)  # no rainbow set for the desk
        os.environ.pop("LIGHT_RAINBOWS")
        self.assertIn("light.nick_strip_lights", status_hub.rainbows())  # Nick's defaults

    def test_voice_toggle_skips_the_settle_wait(self) -> None:
        with patch.object(status_hub, "_settled_state") as settle:
            result = status_hub.home_control("switch.lamp", "on", settle=False)
        settle.assert_not_called()
        self.assertEqual((result["error"], result["state"]), ("", None))


class FanAndVolumeTests(unittest.TestCase):
    """The iPad's fan tile/sheet and room-speaker volume slider."""

    def setUp(self) -> None:
        status_hub.invalidate("entities")
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "t", "HOME_DASHBOARD_ENTITIES": "fan.nicks_fan,light.desk"})
        env.start()
        self.addCleanup(env.stop)
        self.call = patch.object(status_hub, "call_discovered_service",
                                 return_value="Set Percentage command sent.").start()
        self.addCleanup(patch.stopall)
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer t"}

    def test_entities_carry_fan_speed_and_modes(self) -> None:
        states = [{"entity_id": "fan.nicks_fan", "state": "on",
                   "attributes": {"friendly_name": "Nick's Fan", "percentage": 50, "percentage_step": 1.0,
                                  "preset_modes": ["fresh", "nature"], "preset_mode": "fresh"}}]
        with patch.object(status_hub, "get_states", return_value=(True, states)):
            fan = status_hub.home_entities()["entities"][0]["fan"]
        self.assertEqual(fan, {"percentage": 50, "step": 1.0, "preset_modes": ["fresh", "nature"],
                               "preset_mode": "fresh"})

    def test_fan_endpoint_sets_mode_then_speed(self) -> None:
        ok = self.client.post("/api/home/fan", headers=self.headers,
                              json={"entity_id": "fan.nicks_fan", "percentage": 35, "preset_mode": "nature"})
        self.assertEqual((ok.status_code, ok.json()["error"]), (200, ""))
        self.assertEqual([c.args[1] for c in self.call.call_args_list], ["set_preset_mode", "set_percentage"])
        self.assertEqual(self.call.call_args.kwargs["service_data"], {"percentage": 35})
        self.assertEqual(self.client.post("/api/home/fan", headers=self.headers,
                                          json={"entity_id": "light.desk", "percentage": 35}).status_code, 400)
        self.assertEqual(self.client.post("/api/home/fan", headers=self.headers,
                                          json={"entity_id": "fan.nicks_fan"}).status_code, 400)
        self.assertEqual(self.client.post("/api/home/fan", headers=self.headers,
                                          json={"entity_id": "fan.nicks_fan", "percentage": 101}).status_code, 422)

    def test_tv_power_state(self) -> None:
        from brain import wake_alarm
        with patch.object(wake_alarm, "tv_is_on", return_value=True):
            self.assertEqual(self.client.get("/api/tv/power", headers=self.headers).json(), {"on": True})
        with patch.object(wake_alarm, "tv_is_on", side_effect=RuntimeError("HA down")):
            self.assertEqual(self.client.get("/api/tv/power", headers=self.headers).json(), {"on": False})

    def test_room_volume_slider(self) -> None:
        from speech import room_sounds, room_volume
        with patch.object(room_volume, "set_level") as set_level, patch.object(room_sounds, "play") as play, \
                patch.object(room_volume, "level", return_value=6), patch.object(room_volume, "quiet_now",
                                                                                return_value=False):
            got = self.client.get("/api/room/volume", headers=self.headers)
            put = self.client.post("/api/room/volume", headers=self.headers, json={"level": 6})
            bad = self.client.post("/api/room/volume", headers=self.headers, json={"level": 11})
        self.assertEqual(got.json(), {"level": 6, "night": False})
        self.assertEqual(put.json(), {"level": 6, "night": False})
        set_level.assert_called_once_with(6)
        play.assert_called_once_with("chime")
        self.assertEqual(bad.status_code, 422)


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "t", "HOME_DASHBOARD_ENTITIES": "light.desk"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer t"}

    def test_light_endpoint_validates(self) -> None:
        with patch.object(status_hub, "call_discovered_service", return_value="Turn On command sent."):
            ok = self.client.post("/api/home/light", headers=self.headers,
                                  json={"entity_id": "light.desk", "brightness_pct": 40})
            off_list = self.client.post("/api/home/light", headers=self.headers,
                                        json={"entity_id": "light.kitchen", "brightness_pct": 40})
            too_bright = self.client.post("/api/home/light", headers=self.headers,
                                          json={"entity_id": "light.desk", "brightness_pct": 400})
        self.assertEqual((ok.status_code, ok.json()["error"]), (200, ""))
        self.assertEqual(off_list.status_code, 400)
        self.assertEqual(too_bright.status_code, 422)

    def test_scene_endpoint_rejects_non_scenes(self) -> None:
        bad = self.client.post("/api/home/scenes/activate", headers=self.headers, json={"entity_id": "light.desk"})
        self.assertEqual(bad.status_code, 422)
        with patch.object(status_hub, "home_scenes", return_value={"scenes": []}):
            missing = self.client.post("/api/home/scenes/activate", headers=self.headers,
                                       json={"entity_id": "scene.nope"})
        self.assertEqual(missing.status_code, 400)

    def test_im_up_runs_the_good_morning_lights(self) -> None:
        from brain import wake_alarm
        with patch.object(wake_alarm, "im_up") as im_up:
            response = self.client.post("/api/wake/awake", headers=self.headers)
        self.assertEqual(response.json(), {"ok": True})
        im_up.assert_called_once()

        class InlineThread:  # runs the target right away, so nothing races the asserts
            def __init__(self, target, daemon=None, **_):  # noqa: ANN001
                self.target = target

            def start(self) -> None:
                self.target()

        with patch.object(wake_alarm, "_good_morning_logged") as lights, patch.object(wake_alarm, "awake") as awake, \
                patch.object(wake_alarm.threading, "Thread", InlineThread):
            wake_alarm.im_up()
        awake.assert_called_once()
        lights.assert_called_once()

    def test_good_morning_failure_is_logged_not_raised(self) -> None:
        from brain import wake_alarm
        with patch("brain.wake_alarm.good_morning", side_effect=RuntimeError("HA down")), \
                self.assertLogs("jarvis.brain.wake_alarm", level="WARNING"):
            wake_alarm._good_morning_logged()


class ClientTokenCliTests(unittest.TestCase):
    def test_create_grant_list_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(client_tokens, "_FILE", Path(tmp) / "t.json"):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(client_tokens._main(["create", "tv", "events:read"]), 0)
                self.assertEqual(client_tokens._main(["grant", "tv", "wake:write"]), 0)
                self.assertEqual(client_tokens._main(["grant", "nobody", "wake:write"]), 0)
                self.assertEqual(client_tokens._main(["list"]), 0)
                self.assertEqual(client_tokens._main(["revoke", "tv"]), 0)
                self.assertEqual(client_tokens._main(["bogus"]), 2)
            text = out.getvalue()
        self.assertIn("granted", text)
        self.assertIn("no such client", text)
        self.assertIn("tv: events:read, wake:write", text)
        self.assertIn("revoked", text)


if __name__ == "__main__":
    unittest.main()
