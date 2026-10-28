"""The offline brain: keyword intents and their text replies (no Claude)."""
import os
import unittest
from unittest.mock import patch

from brain import intents
from brain.router import route_command

ENTITIES = ["fan.nicks_fan", "light.bedroom", "light.desk", "switch.lamp"]


class OfflineReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entities = patch.object(intents, "list_entities", return_value=(True, ENTITIES)).start()
        patch.object(intents, "speak").start()
        self.addCleanup(patch.stopall)

    def test_routing(self) -> None:
        for said, intent in (("what time is it", "time"), ("list my lights", "list_lights"),
                             ("list devices", "list_devices"), ("turn on the lamp", "device_control"),
                             ("is the lamp on", "home_status"), ("Bye", "exit"), ("goodbye then", "unknown"),
                             ("write me a poem", "unknown")):
            self.assertEqual(route_command(said), intent, said)

    def test_lists_come_from_one_read(self) -> None:
        self.assertEqual(intents.fallback_reply_for_intent("list_lights", ""), "Lights: light.bedroom, light.desk")
        self.assertEqual(intents.fallback_reply_for_intent("list_devices", ""),
                         "Lights: light.bedroom, light.desk Switches: switch.lamp Fans: fan.nicks_fan")
        self.assertEqual(self.entities.call_count, 2)
        self.entities.return_value = (True, [])
        self.assertEqual(intents.fallback_reply_for_intent("list_fans", ""), "Fans: None found.")
        self.entities.return_value = (False, "Could not reach Home Assistant")
        self.assertEqual(intents.fallback_reply_for_intent("list_devices", ""), "Could not reach Home Assistant")

    def test_device_control_and_unknown(self) -> None:
        with patch.object(intents, "handle_device_command", return_value="Turn On command sent.") as control:
            self.assertEqual(intents.fallback_reply("turn on the lamp"), "Turn On command sent.")
        control.assert_called_once_with("turn on the lamp")
        with patch.dict(os.environ, {"ADDRESS": "boss"}):
            self.assertIn("offline, boss", intents.fallback_reply_for_intent("unknown", "poem"))

    def test_a_failing_handler_is_a_sentence(self) -> None:
        with patch.object(intents, "handle_device_command", side_effect=RuntimeError("boom")), \
                self.assertLogs("jarvis.brain.intents", level="ERROR"):
            self.assertIn("Something went wrong", intents.fallback_reply_for_intent("device_control", "x"))

    def test_text_mode_handlers(self) -> None:
        with patch("builtins.print"):
            self.assertFalse(intents.handle_intent("exit", "bye"))
            self.assertTrue(intents.handle_intent("list_devices", "list devices"))
            self.assertTrue(intents.handle_intent("nonsense", "?"))
            with patch.object(intents, "handle_device_command", side_effect=RuntimeError("boom")), \
                    self.assertLogs("jarvis.brain.intents", level="ERROR"):
                self.assertTrue(intents.handle_intent("device_control", "turn on x"))  # never crashes the REPL
        self.assertEqual(self.entities.call_count, 1)


if __name__ == "__main__":
    unittest.main()
