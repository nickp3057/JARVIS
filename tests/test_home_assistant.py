"""Focused tests for Home Assistant command parsing and service dispatch."""

from __future__ import annotations

import os
import json
import threading
import types
import unittest
from unittest.mock import Mock, patch

import requests

from integrations import home_assistant as ha


class HomeAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        ha.clear_services_cache()  # the catalogue cache must not leak between tests
        self.addCleanup(ha.clear_services_cache)

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

    @patch.object(ha, "_find_best_entity", return_value=(True, "light.desk_light"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_brightness_uses_unique_light_and_validated_percentage(
        self, call_service: Mock, _: Mock,
    ) -> None:
        self.assertEqual(ha.set_light_brightness("desk lamp", 42), "sent")
        call_service.assert_called_once_with(
            "light", "turn_on", "light.desk_light", {"brightness_pct": 42},
        )
        self.assertIn("between 0 and 100", ha.set_light_brightness("desk", 101))

    @patch.object(ha, "_find_best_entity", return_value=(True, "light.desk_light"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_zero_brightness_turns_light_off(self, call_service: Mock, _: Mock) -> None:
        self.assertEqual(ha.set_light_brightness("desk", 0), "sent")
        call_service.assert_called_once_with("light", "turn_off", "light.desk_light", {})

    @patch.object(ha, "_find_best_entity", return_value=(True, "light.desk_light"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_named_color_uses_unique_light_and_allowlist(
        self, call_service: Mock, _: Mock,
    ) -> None:
        self.assertEqual(ha.set_light_color("desk lamp", " Purple "), "sent")
        call_service.assert_called_once_with(
            "light", "turn_on", "light.desk_light", {"color_name": "purple"},
        )

    @patch.object(ha, "_find_best_entity")
    def test_named_color_rejects_unsupported_value_before_matching(self, find_entity: Mock) -> None:
        self.assertIn("not in", ha.set_light_color("desk", "ultraviolet"))
        find_entity.assert_not_called()

    @patch.object(ha, "_find_best_entity", return_value=(False, "multiple matches"))
    def test_named_color_preserves_ambiguity_without_service_call(self, _: Mock) -> None:
        with patch.object(ha, "call_service") as call_service:
            self.assertEqual(ha.set_light_color("bedroom", "red"), "multiple matches")
        call_service.assert_not_called()

    @patch.object(ha, "_find_best_entity", return_value=(True, "climate.main"))
    @patch.object(ha, "call_service", return_value=(True, "sent"))
    def test_climate_temperature_converts_to_thermostat_unit(
        self, call_service: Mock, _: Mock,
    ) -> None:
        states = [{
            "entity_id": "climate.main",
            "attributes": {"temperature_unit": "°C"},
        }]
        with patch.object(ha, "get_states", return_value=(True, states)):
            self.assertEqual(ha.set_climate_temperature("main thermostat", 68, "F"), "sent")
        call_service.assert_called_once_with(
            "climate", "set_temperature", "climate.main", {"temperature": 20.0},
        )

    @patch.object(ha, "_find_best_entity", return_value=(True, "climate.main"))
    @patch.object(ha, "call_service", return_value=(False, "offline"))
    def test_climate_temperature_rejects_unsafe_inputs_and_reports_failures(
        self, call_service: Mock, find_entity: Mock,
    ) -> None:
        self.assertIn("between 45 and 90", ha.set_climate_temperature("main", 120, "F"))
        self.assertIn("valid Fahrenheit or Celsius", ha.set_climate_temperature("main", 70, "K"))
        find_entity.assert_not_called()
        states = [{"entity_id": "climate.main", "attributes": {"temperature_unit": "F"}}]
        with patch.object(ha, "get_states", return_value=(True, states)):
            self.assertEqual(
                ha.set_climate_temperature("main", 72, "F"),
                "Command failed. offline",
            )
        call_service.assert_called_once()

    @patch.object(ha, "_find_best_entity", return_value=(True, "climate.main"))
    def test_climate_temperature_fails_closed_when_live_unit_is_unknown(self, _: Mock) -> None:
        states = [{"entity_id": "climate.main", "attributes": {}}]
        with patch.object(ha, "get_states", return_value=(True, states)), patch.object(
            ha, "call_service",
        ) as call_service:
            result = ha.set_climate_temperature("main", 72, "F")
        self.assertIn("determine", result)
        call_service.assert_not_called()

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

    @patch.object(ha, "get_services")
    def test_list_services_uses_live_catalogue(self, get_services: Mock) -> None:
        get_services.return_value = (
            True,
            [
                {"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
                {"domain": "climate", "services": {"set_temperature": {}}},
            ],
        )
        self.assertEqual(
            ha.list_services("light"),
            (True, ["light.turn_off", "light.turn_on"]),
        )

    def test_sensitive_service_requires_explicit_confirmation(self) -> None:
        result = ha.call_discovered_service(
            "lock", "unlock", entity_id="lock.front_door",
        )
        self.assertIn("Confirmation required", result)

    def test_administrative_service_is_blocked(self) -> None:
        result = ha.call_discovered_service("homeassistant", "restart", confirmed=True)
        self.assertIn("blocked", result)

    @patch.object(ha, "call_service", return_value=(True, "sent"))
    @patch.object(ha, "list_services", return_value=(True, ["light.turn_on"]))
    def test_discovered_service_calls_advertised_action(
        self, _: Mock, call_service: Mock,
    ) -> None:
        result = ha.call_discovered_service(
            "light",
            "turn_on",
            entity_id="light.office",
            service_data={"brightness_pct": 40},
        )
        self.assertEqual(result, "sent")
        call_service.assert_called_once_with(
            "light", "turn_on", "light.office", {"brightness_pct": 40},
        )

    def test_websocket_url_tracks_http_security(self) -> None:
        self.assertEqual(
            ha._websocket_url("https://ha.example/base/"),
            "wss://ha.example/base/api/websocket",
        )

    @patch.object(ha, "_request", return_value=(True, {"result": "ok"}))
    def test_automation_config_crud_routes(self, request: Mock) -> None:
        config = {"alias": "Test"}
        ha.get_automation_config("123")
        ha.save_automation_config("123", config)
        ha.delete_automation_config("123")
        self.assertEqual(
            request.call_args_list[0].args,
            ("GET", "/api/config/automation/config/123"),
        )
        self.assertEqual(
            request.call_args_list[1].args,
            ("POST", "/api/config/automation/config/123"),
        )
        self.assertEqual(request.call_args_list[1].kwargs, {"json": config})
        self.assertEqual(
            request.call_args_list[2].args,
            ("DELETE", "/api/config/automation/config/123"),
        )

    def test_live_state_subscription_authenticates_and_dispatches(self) -> None:
        stop = threading.Event()
        received: list[dict] = []

        class FakeSocket:
            def __init__(self) -> None:
                self.sent: list[dict] = []
                self.messages = iter((
                    json.dumps({"type": "auth_required"}),
                    json.dumps({"type": "auth_ok"}),
                    json.dumps({"id": 1, "type": "result", "success": True}),
                    json.dumps({
                        "id": 1,
                        "type": "event",
                        "event": {"data": {"entity_id": "light.office"}},
                    }),
                ))

            def recv(self) -> str:
                return next(self.messages)

            def send(self, payload: str) -> None:
                self.sent.append(json.loads(payload))

            def settimeout(self, _: float) -> None:
                pass

            def close(self) -> None:
                pass

        socket = FakeSocket()
        fake_websocket = types.SimpleNamespace(
            create_connection=lambda *_args, **_kwargs: socket,
            WebSocketTimeoutException=TimeoutError,
        )

        def on_change(data: dict) -> None:
            received.append(data)
            stop.set()

        env = {
            "HOME_ASSISTANT_URL": "http://ha.local:8123",
            "HOME_ASSISTANT_TOKEN": "secret",
        }
        with patch.dict(os.environ, env, clear=True), patch.dict(
            "sys.modules", {"websocket": fake_websocket},
        ):
            ha.listen_state_changes(stop, on_change)

        self.assertEqual(received, [{"entity_id": "light.office"}])
        self.assertEqual(socket.sent[0], {"type": "auth", "access_token": "secret"})
        self.assertEqual(socket.sent[1]["type"], "subscribe_events")

    def test_request_handles_missing_config_network_and_text_success(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            ok, error = ha._request("GET", "/api/")
        self.assertFalse(ok)
        self.assertIn("not configured", error)

        env = {"HOME_ASSISTANT_URL": "http://ha", "HOME_ASSISTANT_TOKEN": "token"}
        with patch.dict(os.environ, env, clear=True), patch.object(
            ha._session, "request", side_effect=requests.ConnectionError("offline"),
        ):
            ok, error = ha._request("GET", "/api/")
        self.assertFalse(ok)
        self.assertIn("offline", error)

        response = Mock(status_code=200, text="ok")
        response.json.side_effect = ValueError
        with patch.dict(os.environ, env, clear=True), patch.object(
            ha._session, "request", return_value=response,
        ):
            self.assertEqual(ha._request("GET", "/api/"), (True, "ok"))

    def test_list_entities_and_services_reject_unexpected_payloads(self) -> None:
        with patch.object(ha, "get_states", return_value=(True, {"not": "a list"})):
            ok, result = ha.list_entities()
        self.assertFalse(ok)
        self.assertIn("Unexpected", result)

    def test_home_status_filters_live_states_and_uses_friendly_names(self) -> None:
        states = [
            {"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk Lamp"}},
            {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen Light"}},
            {"entity_id": "fan.bedroom", "state": "on", "attributes": {"friendly_name": "Bedroom Fan"}},
            {"entity_id": "climate.main", "state": "heat", "attributes": {
                "friendly_name": "Main Thermostat", "current_temperature": 71,
                "temperature_unit": "°F",
            }},
        ]
        with patch.object(ha, "get_states", return_value=(True, states)):
            self.assertEqual(ha.describe_home_status("which lights are on"), "Desk Lamp is on.")
            self.assertEqual(ha.describe_home_status("is kitchen light off"), "Kitchen Light is off.")
            self.assertEqual(ha.describe_home_status("what fans are running"), "Bedroom Fan is on.")
            self.assertEqual(
                ha.describe_home_status("main thermostat"),
                "Main Thermostat is heat, 71°F.",
            )

    def test_home_status_handles_failures_and_unexpected_payloads(self) -> None:
        with patch.object(ha, "get_states", return_value=(False, "Home Assistant is offline.")):
            self.assertEqual(ha.describe_home_status("lights"), "Home Assistant is offline.")
        with patch.object(ha, "get_states", return_value=(True, {"bad": "payload"})):
            self.assertIn("Unexpected", ha.describe_home_status("lights"))
        with patch.object(ha, "get_services", return_value=(True, {"not": "a list"})):
            ok, result = ha.list_services()
        self.assertFalse(ok)
        self.assertIn("Unexpected", result)

    def test_general_service_validation_and_discovery_failures(self) -> None:
        self.assertIn("Invalid", ha.call_discovered_service("bad domain", "turn_on"))
        self.assertIn("Invalid", ha.call_discovered_service(
            "light", "turn_on", entity_id="not-an-entity",
        ))
        self.assertIn("must be an object", ha.call_discovered_service(
            "light", "turn_on", service_data="bad",  # type: ignore[arg-type]
        ))
        with patch.object(ha, "list_services", return_value=(False, "offline")):
            self.assertIn("offline", ha.call_discovered_service("light", "turn_on"))
        with patch.object(ha, "list_services", return_value=(True, ["light.turn_off"])):
            self.assertIn("does not advertise", ha.call_discovered_service("light", "turn_on"))

    def test_service_payload_combines_data_and_explicit_target(self) -> None:
        with patch.object(ha, "_request", return_value=(True, [])) as request:
            ok, _ = ha.call_service(
                "light", "turn_on", "light.office", {"brightness_pct": 25, "entity_id": "wrong"},
            )
        self.assertTrue(ok)
        self.assertEqual(
            request.call_args.kwargs["json"],
            {"brightness_pct": 25, "entity_id": "light.office"},
        )

    def test_confirmation_classifier_covers_sensitive_actions(self) -> None:
        for domain, service in (
            ("lock", "lock"),
            ("cover", "close_cover"),
            ("light", "open_something"),
            ("alarm_control_panel", "alarm_arm_home"),
        ):
            with self.subTest(domain=domain, service=service):
                self.assertTrue(ha.service_requires_confirmation(domain, service))
        self.assertFalse(ha.service_requires_confirmation("light", "turn_on"))

    def test_websocket_rejects_bad_greeting_auth_and_subscription(self) -> None:
        class Socket:
            def __init__(self, messages: list[dict]) -> None:
                self.messages = iter(json.dumps(message) for message in messages)

            def recv(self) -> str:
                return next(self.messages)

            def send(self, _: str) -> None:
                pass

            def close(self) -> None:
                pass

        env = {"HOME_ASSISTANT_URL": "http://ha", "HOME_ASSISTANT_TOKEN": "token"}
        cases = (
            ([{"type": "wrong"}], "greeting"),
            ([{"type": "auth_required"}, {"type": "auth_invalid", "message": "bad"}], "authentication"),
            ([
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"id": 1, "type": "result", "success": False},
            ], "rejected"),
        )
        for messages, expected in cases:
            fake = types.SimpleNamespace(
                create_connection=lambda *_args, _messages=messages, **_kwargs: Socket(_messages),
                WebSocketTimeoutException=TimeoutError,
            )
            with self.subTest(expected=expected), patch.dict(
                os.environ, env, clear=True,
            ), patch.dict("sys.modules", {"websocket": fake}):
                with self.assertRaisesRegex(RuntimeError, expected):
                    ha.listen_state_changes(threading.Event(), lambda _: None)

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
