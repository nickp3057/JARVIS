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
    {"entity_id": "light.nicks_fan", "friendly_name": "Fan Light", "domain": "light", "state": "off"},
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
        self.fan = patch.object(status_hub, "set_fan", return_value={"error": ""}).start()
        self.addCleanup(patch.stopall)

    def test_scenes_by_name(self) -> None:
        for said, scene in (("Game time.", "scene.game_time"), ("Good night, Jarvis", "scene.goodnight"),
                            ("peace", "scene.peace"), ("run the game time scene", "scene.game_time")):
            self.scene.reset_mock()
            result = quick_commands.run(said)
            self.assertTrue(result and result.ok, said)
            self.scene.assert_called_once_with(scene)

    def test_tv_power_by_voice(self) -> None:
        with patch("integrations.tv_remote.power", return_value=(True, "TV off, sir.")) as power:
            for said, on in (("Turn off the TV.", False), ("TV on", True), ("turn the television off", False)):
                power.reset_mock()
                result = quick_commands.run(said)
                self.assertTrue(result and result.ok, said)
                power.assert_called_once_with(on)
        self.control.assert_not_called()

    def test_on_off_and_brightness(self) -> None:
        quick_commands.run("Turn off the desk lights.")
        self.control.assert_called_with("light.desk_lights_2", "off", settle=False)  # no 2 s wait
        quick_commands.run("lamp on")
        self.control.assert_called_with("switch.lamp", "on", settle=False)
        quick_commands.run("turn the fan on")
        self.control.assert_called_with("fan.nicks_fan", "on", settle=False)
        quick_commands.run("set the bedroom light to 40 percent")
        self.light.assert_called_with("light.bedroom_lights", brightness_pct=40)

    def test_fan_speed_never_touches_the_fan_light(self) -> None:
        for said in ("Set fan to 30", "Set fan to 30%.", "fan at 30 percent"):
            self.fan.reset_mock()
            result = quick_commands.run(said)
            self.assertTrue(result and result.ok, said)
            self.fan.assert_called_once_with("fan.nicks_fan", 30)
        self.light.assert_not_called()

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

    def test_announcement_from_idle_drops_last_turns_words(self) -> None:
        voice_presence.update("listening", transcript="download iron man")
        voice_presence.update("speaking", reply="On it, sir.")
        voice_presence.update("idle")
        voice_presence.update("speaking", reply="Iron Man has finished downloading, sir.")
        state = voice_presence.current()
        self.assertEqual((state["transcript"], state["reply"]), ("", "Iron Man has finished downloading, sir."))

    def test_answer_keeps_what_was_asked(self) -> None:
        voice_presence.update("listening", transcript="what time is it")
        voice_presence.update("thinking")
        voice_presence.update("speaking", reply="Half past nine, sir.")
        self.assertEqual(voice_presence.current()["transcript"], "what time is it")

    def test_state_endpoint(self) -> None:
        voice_presence.update("listening", transcript="game time")
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "t"}):
            body = TestClient(server.app).get("/api/voice/state", headers={"Authorization": "Bearer t"}).json()
        self.assertEqual((body["phase"], body["transcript"]), ("listening", "game time"))


if __name__ == "__main__":
    unittest.main()


class RoomAlertTests(unittest.TestCase):
    """Phone alerts are also said in the room (speech/room_voice._on_alert)."""

    def _alert(self, **over):
        from brain.event_bus import Alert
        fields = dict(id=1, kind="temp_4k_ready", severity="info", title="Dune is ready in 4K",
                      message="Dune (2021) is in Plex in 4K; pick the 4K version. It goes back tomorrow.",
                      source="media", created_at=0.0, push=True)
        fields.update(over)
        return Alert(**fields)

    def _run(self, alert, quiet=False, env=None):
        from unittest import mock
        from speech import room_voice
        voice = mock.Mock()
        with mock.patch.object(room_voice, "_voice", voice), \
                mock.patch.object(room_voice.room_volume, "quiet_now", return_value=quiet), \
                mock.patch.dict("os.environ", env or {}, clear=False):
            room_voice._on_alert(alert)
        return voice.broadcast.call_args_list

    def test_push_alert_is_said(self) -> None:
        calls = self._run(self._alert())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], "Dune (2021) is in Plex in 4K; pick the 4K version. It goes back tomorrow.")

    def test_skips_quiet_passive_wake_and_no_push(self) -> None:
        self.assertEqual(self._run(self._alert(), quiet=True), [])
        self.assertEqual(self._run(self._alert(push=False)), [])
        self.assertEqual(self._run(self._alert(grouping={"passive": True})), [])
        self.assertEqual(self._run(self._alert(kind="wake_up")), [])
        self.assertEqual(self._run(self._alert(), env={"ROOM_ALERTS": "0"}), [])

    def test_title_when_no_message(self) -> None:
        calls = self._run(self._alert(message=""))
        self.assertEqual(calls[0].args[0], "Dune is ready in 4K")
