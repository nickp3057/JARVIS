"""TV power and the companion remote go through the TV's own remote protocol."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
from brain import tools
from integrations import home_assistant, tv_remote


class TvRemoteTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin"})
        env.start()
        self.addCleanup(env.stop)
        request = patch.object(home_assistant, "_request", return_value=(True, []))
        self.request = request.start()
        self.addCleanup(request.stop)

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
        with patch.object(plex, "_get", side_effect=lambda path, params=None: (True, responses[path])), \
                patch.object(home_assistant, "_request", return_value=(True, [])) as request:
            reply = tools.execute_tool("play_on_tv", {"title": "suits"})
        self.assertIn('season 6, episode 15, "Quid Pro Quo"', reply)
        sent = request.call_args.kwargs["json"]
        self.assertEqual(sent["entity_id"], "remote.nick_s_room_tv")
        self.assertEqual(sent["activity"],
                         "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F8330&server=srv")
