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
