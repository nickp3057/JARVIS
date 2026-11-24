"""TV power and the companion remote go through the TV's own remote protocol."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
from brain import tools
import asyncio

from integrations import home_assistant, tv_direct, tv_remote


class TvRemoteTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin"})
        env.start()
        self.addCleanup(env.stop)
        request = patch.object(home_assistant, "_request", return_value=(True, []))
        self.request = request.start()
        self.addCleanup(request.stop)
        direct = patch.object(tv_direct, "configured", return_value=False)
        self.direct = direct.start()
        self.addCleanup(direct.stop)

    def test_direct_connection_skips_home_assistant(self) -> None:
        self.direct.return_value = True
        with patch.object(tv_direct, "press_key", return_value=(True, "ok")) as key, \
                patch.object(tv_direct, "launch", return_value=(True, "ok")) as launch:
            self.assertTrue(tv_remote.press("up")[0])
            self.assertTrue(tv_remote.press("app_plex")[0])
        key.assert_called_once_with("DPAD_UP")
        launch.assert_called_once_with("com.plexapp.android")
        self.request.assert_not_called()

    def test_direct_failure_falls_back_to_home_assistant(self) -> None:
        self.direct.return_value = True
        with patch.object(tv_direct, "press_key", return_value=(False, "the TV didn't answer in time")):
            self.assertTrue(tv_remote.press("ok")[0])
        self.request.assert_called_once_with("POST", "/api/services/remote/send_command", json={
            "entity_id": "remote.nick_s_room_tv", "command": "DPAD_CENTER"})

    def test_direct_power_only_toggles_when_needed(self) -> None:
        class Fake:
            def __init__(self, is_on):
                self.is_on, self.sent = is_on, []

            def send_key_command(self, code):
                self.sent.append(code)
        for is_on, on, expected in ((True, True, []), (False, True, ["POWER"]), (True, False, ["POWER"]),
                                    (None, True, ["WAKEUP"]), (None, False, ["SLEEP"])):
            fake = Fake(is_on)
            asyncio.run(tv_direct._set_power(fake, on))
            self.assertEqual(fake.sent, expected, (is_on, on))

    def test_cast_turn_on_becomes_a_remote_power_on(self) -> None:
        ok, message = home_assistant.call_service("media_player", "turn_on", "media_player.nicks_room_tv")
        self.assertTrue(ok)
        self.request.assert_called_once_with(
            "POST", "/api/services/remote/turn_on", json={"entity_id": "remote.nick_s_room_tv"})
        self.assertEqual(message, "TV on, sir.")

    def test_input_buttons(self) -> None:
        tv_remote.press("hdmi_2")
        self.request.assert_called_with("POST", "/api/services/remote/turn_on", json={
            "entity_id": "remote.nick_s_room_tv",
            "activity": "content://android.media.tv/passthrough/com.tcl.tvinput%2F.TvPassThroughService%2FHW16"})
        with self.assertRaises(ValueError):
            tv_remote.press("hdmi_4")

    def test_other_media_players_are_untouched(self) -> None:
        home_assistant.call_service("media_player", "turn_on", "media_player.zacks_tv")
        self.request.assert_called_once_with(
            "POST", "/api/services/media_player/turn_on", json={"entity_id": "media_player.zacks_tv"})

    def test_tool_and_remote_buttons(self) -> None:
        self.assertEqual(tools.execute_tool("tv_power", {"state": "off"}), "TV off, sir.")
        client = TestClient(server.app)
        self.addCleanup(client.close)
        admin = {"Authorization": "Bearer admin"}
        self.assertEqual(client.post("/api/tv/remote", json={"key": "up"}).status_code, 401)
        self.assertEqual(client.post("/api/tv/remote", headers=admin, json={"key": "ok"}).json(), {"ok": True})
        self.request.assert_called_with("POST", "/api/services/remote/send_command",
                                        json={"command": "DPAD_CENTER", "entity_id": "remote.nick_s_room_tv"})
        self.assertEqual(client.post("/api/tv/remote", headers=admin, json={"key": "eject"}).status_code, 422)
        self.request.return_value = (False, "unavailable")
        self.assertEqual(client.post("/api/tv/remote", headers=admin, json={"key": "home"}).status_code, 502)


if __name__ == "__main__":
    unittest.main()


class PlayOnTvTests(unittest.TestCase):
    def test_show_continues_its_on_deck_episode(self) -> None:
        responses = {
            "/identity": {"MediaContainer": {"machineIdentifier": "srv"}},
            "/hubs/search": {"MediaContainer": {"Hub": [{"Metadata": [
                {"type": "show", "title": "Suits", "ratingKey": "66"}]}]}},
            "/library/onDeck": {"MediaContainer": {"Metadata": [
                {"type": "movie", "title": "Cars 2", "ratingKey": "1"},
                {"type": "episode", "grandparentRatingKey": "66", "grandparentTitle": "Suits",
                 "parentIndex": 6, "index": 15, "title": "Quid Pro Quo", "ratingKey": "8330"}]}},
        }
        from integrations import plex
        from integrations import tv_remote
        with patch.object(plex, "_get", side_effect=lambda path, params=None: (True, responses[path])), \
                patch.object(tv_remote, "play_item", return_value=(True, "")) as play:
            reply = tools.execute_tool("play_on_tv", {"title": "suits"})
        self.assertIn('season 6, episode 15, "Quid Pro Quo"', reply)
        # JARVIS TV opens the show's page (its Watch plays that next-up
        # episode); HA's plex:// link was ignored while Plex was open.
        play.assert_called_once_with("66", "show", "Suits")
