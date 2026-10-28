"""Room voice: the quick home commands, the live state and its endpoints."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server, status_hub
from brain import voice_presence
from speech import quick_commands

SCENES = {"scenes": [{"entity_id": "scene.game_time", "name": "Game Time"},
                     {"entity_id": "scene.goodnight", "name": "Goodnight"},
                     {"entity_id": "scene.peace", "name": "Peace"}], "last": None, "error": ""}
ENTITIES = {"entities": [
    {"entity_id": "light.desk_lights_2", "friendly_name": "Desk Lights", "domain": "light", "state": "off"},
    {"entity_id": "light.bedroom_lights", "friendly_name": "Bedroom Lights", "domain": "light", "state": "off"},
    {"entity_id": "fan.nicks_fan", "friendly_name": "Nick’s Fan", "domain": "fan", "state": "off"},
    {"entity_id": "switch.lamp", "friendly_name": "Lamp", "domain": "switch", "state": "off"},
], "error": ""}


class QuickCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in (("home_scenes", SCENES), ("home_entities", ENTITIES)):
            p = patch.object(status_hub, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.scene = patch.object(status_hub, "activate_scene", return_value={"error": ""}).start()
        self.control = patch.object(status_hub, "home_control", return_value={"error": ""}).start()
        self.light = patch.object(status_hub, "set_light", return_value={"error": ""}).start()
        self.addCleanup(patch.stopall)

    def test_scenes_by_name(self) -> None:
        for said, scene in (("Game time.", "scene.game_time"), ("Good night, Jarvis", "scene.goodnight"),
                            ("peace", "scene.peace"), ("run the game time scene", "scene.game_time")):
            self.scene.reset_mock()
            result = quick_commands.run(said)
            self.assertTrue(result and result.ok, said)
            self.scene.assert_called_once_with(scene)

    def test_on_off_and_brightness(self) -> None:
        quick_commands.run("Turn off the desk lights.")
        self.control.assert_called_with("light.desk_lights_2", "off", settle=False)  # no 2 s wait
        quick_commands.run("lamp on")
        self.control.assert_called_with("switch.lamp", "on", settle=False)
        quick_commands.run("turn the fan on")
        self.control.assert_called_with("fan.nicks_fan", "on", settle=False)
        quick_commands.run("set the bedroom light to 40 percent")
        self.light.assert_called_with("light.bedroom_lights", brightness_pct=40)

    def test_anything_else_goes_to_the_conversation(self) -> None:
        for said in ("what's the weather tomorrow", "turn it off", "turn off the lights",
                     "tell me a long story about the history of the roman empire please"):
            self.assertIsNone(quick_commands.run(said), said)
        self.control.assert_not_called()
        self.scene.assert_not_called()


class VoicePresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()

    def test_listening_clears_old_text_and_seq_moves(self) -> None:
        voice_presence.update("listening", transcript="turn on")
        voice_presence.update("speaking", reply="Done.")
        voice_presence.update("listening")
        state = voice_presence.current()
        self.assertEqual((state["phase"], state["transcript"], state["reply"]), ("listening", "", ""))
        self.assertEqual(voice_presence.wait(state["seq"] - 1, 0.01)["seq"], state["seq"])
        with self.assertRaises(ValueError):
            voice_presence.update("dancing")

    def test_state_endpoint(self) -> None:
        voice_presence.update("listening", transcript="game time")
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "t"}):
            body = TestClient(server.app).get("/api/voice/state", headers={"Authorization": "Bearer t"}).json()
        self.assertEqual((body["phase"], body["transcript"]), ("listening", "game time"))


if __name__ == "__main__":
    unittest.main()
