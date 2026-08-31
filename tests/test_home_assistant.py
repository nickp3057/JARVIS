"""Focused tests for Home Assistant command parsing and service dispatch."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from integrations import home_assistant as ha


class HomeAssistantTests(unittest.TestCase):
    def test_explicit_off_wins_over_location_word_on(self) -> None:
        samples = (
            "turn off the light on my desk",
            "turn the lights on my desk off",
            "switch off the fan on the nightstand",
            "turn the lights back off",
        )
        for command in samples:
            with self.subTest(command=command):
                self.assertEqual(ha._extract_action(command), "turn_off")

    def test_on_commands_still_parse(self) -> None:
        for command in ("turn on the desk light", "switch the fan on"):
            with self.subTest(command=command):
                self.assertEqual(ha._extract_action(command), "turn_on")

    @patch.object(ha, "_find_best_entity", return_value=(True, "light.desk_light"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_off_command_calls_turn_off(self, call_service: Mock, _: Mock) -> None:
        self.assertEqual(ha.handle_device_command("turn off the light on my desk"), "sent")
        call_service.assert_called_once_with("light", "turn_off", "light.desk_light")

    @patch.object(ha, "_find_best_entity", return_value=(True, "scene.good_night"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_scene_activation(self, call_service: Mock, find_entity: Mock) -> None:
        self.assertEqual(ha.activate_home_action("scenes", "good night"), "sent")
        find_entity.assert_called_once_with("good night", ["scene"])
        call_service.assert_called_once_with("scene", "turn_on", "scene.good_night")

    @patch.object(ha, "_find_best_entity", return_value=(True, "automation.bedtime"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_automation_trigger(self, call_service: Mock, _: Mock) -> None:
        self.assertEqual(ha.activate_home_action("automation", "bedtime"), "sent")
        call_service.assert_called_once_with("automation", "trigger", "automation.bedtime")

    def test_short_config_names_are_supported(self) -> None:
        env = {
            "HA_URL": "http://homeassistant.local:8123/",
            "HA_TOKEN": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                ha._config(),
                ("http://homeassistant.local:8123", "secret"),
            )

    def test_long_config_names_take_precedence(self) -> None:
        env = {
            "HOME_ASSISTANT_URL": "http://preferred:8123",
            "HOME_ASSISTANT_TOKEN": "preferred-token",
            "HA_URL": "http://legacy:8123",
            "HA_TOKEN": "legacy-token",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                ha._config(),
                ("http://preferred:8123", "preferred-token"),
            )


if __name__ == "__main__":
    unittest.main()
