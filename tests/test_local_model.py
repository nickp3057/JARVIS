"""Optional local conversational routing and safe keyword precedence."""

from __future__ import annotations

import os
from threading import RLock
import unittest
from unittest.mock import Mock, patch

import requests

from brain import local_model
from brain.agent import JarvisAgent
from brain.intents import routed_offline_reply
from brain.router import route_command


class LocalModelTests(unittest.TestCase):
    def setUp(self) -> None:
        local_model._unavailable_until = 0.0

    def _env(self, **overrides: str):
        values = {
            "LOCAL_MODEL_ENABLED": "true",
            "LOCAL_MODEL_URL": "http://127.0.0.1:11434",
            "LOCAL_MODEL_NAME": "qwen-test",
            "LOCAL_MODEL_TIMEOUT_S": "3",
            **overrides,
        }
        return patch.dict(os.environ, values, clear=False)

    def test_requires_explicit_enable_url_and_model(self) -> None:
        with patch.dict(os.environ, {"LOCAL_MODEL_ENABLED": "false"}, clear=True):
            self.assertFalse(local_model.is_configured())
        with patch.dict(os.environ, {"LOCAL_MODEL_ENABLED": "true"}, clear=True):
            self.assertFalse(local_model.is_configured())

    def test_openai_compatible_request_is_bounded_and_validated(self) -> None:
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "At your service, sir."}}]
        }
        with self._env(LOCAL_MODEL_API_KEY="secret"), patch(
            "brain.local_model.requests.post", return_value=response
        ) as post:
            reply = local_model.generate_reply(
                [{"role": "user", "content": "old"}] * 20,
                "Hello",
            )
        self.assertEqual(reply, "At your service, sir.")
        request = post.call_args
        self.assertEqual(request.args[0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(request.kwargs["timeout"], 3.0)
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertLessEqual(len(request.kwargs["json"]["messages"]), 14)
        self.assertEqual(request.kwargs["json"]["model"], "qwen-test")

    def test_existing_completion_path_is_not_duplicated(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "Ready."}}]}
        with self._env(LOCAL_MODEL_URL="http://localhost:9000/v1/chat/completions"), patch(
            "brain.local_model.requests.post", return_value=response
        ) as post:
            self.assertEqual(local_model.generate_reply([], "Hello"), "Ready.")
        self.assertEqual(post.call_args.args[0], "http://localhost:9000/v1/chat/completions")

    def test_network_malformed_and_invalid_url_fail_closed(self) -> None:
        with self._env(), patch(
            "brain.local_model.requests.post",
            side_effect=requests.ConnectionError("offline"),
        ):
            self.assertIsNone(local_model.generate_reply([], "Hello"))
        local_model._unavailable_until = 0.0
        bad = Mock()
        bad.json.return_value = {"unexpected": True}
        with self._env(), patch("brain.local_model.requests.post", return_value=bad):
            self.assertIsNone(local_model.generate_reply([], "Hello"))
        with self._env(LOCAL_MODEL_URL="file:///tmp/model"):
            self.assertIsNone(local_model.generate_reply([], "Hello"))

    def test_failure_circuit_prevents_two_slow_attempts(self) -> None:
        with self._env(LOCAL_MODEL_FAILURE_COOLDOWN_S="45"), patch(
            "brain.local_model.requests.post",
            side_effect=requests.Timeout("slow"),
        ) as post:
            self.assertIsNone(local_model.classify_read_only_intent("Tell me a joke"))
            self.assertIsNone(local_model.generate_reply([], "Tell me a joke"))
        post.assert_called_once()

    def test_semantic_classifier_accepts_only_exact_read_only_intents(self) -> None:
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": '```json\n{"kind":"intent","intent":"list_lights"}\n```'}}]
        }
        with self._env(), patch("brain.local_model.requests.post", return_value=response):
            self.assertEqual(
                local_model.classify_read_only_intent("Show me every lamp"),
                "list_lights",
            )

        for unsafe in ("device_control", "delete_session", "list_lights now"):
            local_model._unavailable_until = 0.0
            response.json.return_value = {
                "choices": [{"message": {"content": f'{{"kind":"intent","intent":"{unsafe}"}}'}}]
            }
            with self._env(), patch("brain.local_model.requests.post", return_value=response):
                self.assertIsNone(local_model.classify_read_only_intent("Do something"))

    def test_semantic_routing_executes_deterministic_read_only_handler(self) -> None:
        with patch(
            "brain.local_model.classify_semantic_route",
            return_value={"kind": "intent", "intent": "list_lights"},
        ), patch("brain.intents.list_entities",
                 return_value=(True, ["light.desk_lamp", "switch.wii_u"])) as listing, patch(
            "brain.local_model.generate_reply"
        ) as conversation:
            reply = routed_offline_reply("Show me every lamp", [])
        self.assertEqual(reply, "Lights: light.desk_lamp")  # only the lights
        listing.assert_called_once_with()
        conversation.assert_not_called()

    def test_semantic_status_route_uses_original_question(self) -> None:
        with patch(
            "brain.local_model.classify_semantic_route",
            return_value={"kind": "intent", "intent": "home_status"},
        ), patch(
            "brain.intents.describe_home_status", return_value="Desk Lamp is on."
        ) as status, patch("brain.local_model.generate_reply") as conversation:
            reply = routed_offline_reply("Which desk lights are active?", [])
        self.assertEqual(reply, "Desk Lamp is on.")
        status.assert_called_once_with("Which desk lights are active?")
        conversation.assert_not_called()

    def test_keyword_status_questions_do_not_become_device_commands(self) -> None:
        self.assertEqual(route_command("is the kitchen light off"), "home_status")
        self.assertEqual(route_command("which fans are running"), "home_status")
        self.assertEqual(route_command("turn the kitchen light off"), "device_control")

    def test_semantic_routing_can_be_disabled(self) -> None:
        with self._env(LOCAL_MODEL_SEMANTIC_ROUTING="false"), patch(
            "brain.local_model.requests.post"
        ) as post:
            self.assertIsNone(local_model.classify_read_only_intent("Show the lamps"))
        post.assert_not_called()

    def test_high_confidence_reversible_home_command_is_structured(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"kind":"home_control","action":"turn_off",'
            '"target":"desk lamp","confidence":0.96}'
        )}}]}
        with self._env(), patch("brain.local_model.requests.post", return_value=response):
            route = local_model.classify_semantic_route("Kill the desk lamp")
        self.assertEqual(
            route,
            {"kind": "home_control", "action": "turn_off", "target": "desk lamp"},
        )

    def test_home_command_rejects_low_confidence_unrelated_and_sensitive_targets(self) -> None:
        response = Mock()
        cases = (
            (
                "Maybe kill the desk lamp",
                '{"kind":"home_control","action":"turn_off","target":"desk lamp","confidence":0.4}',
            ),
            (
                "Turn something off",
                '{"kind":"home_control","action":"turn_off","target":"bedroom fan","confidence":0.99}',
            ),
            (
                "Secure the front door",
                '{"kind":"home_control","action":"turn_on","target":"front door lock","confidence":0.99}',
            ),
            (
                "Play music",
                '{"kind":"home_control","action":"play","target":"music","confidence":0.99}',
            ),
        )
        for user_input, content in cases:
            with self.subTest(user_input=user_input):
                local_model._unavailable_until = 0.0
                response.json.return_value = {"choices": [{"message": {"content": content}}]}
                with self._env(), patch("brain.local_model.requests.post", return_value=response):
                    self.assertIsNone(local_model.classify_semantic_route(user_input))

    def test_structured_home_route_uses_existing_unique_entity_control(self) -> None:
        route = {"kind": "home_control", "action": "turn_off", "target": "desk lamp"}
        with patch("brain.local_model.classify_semantic_route", return_value=route), patch(
            "brain.intents.handle_device_command", return_value="Desk lamp turned off."
        ) as control, patch("brain.local_model.generate_reply") as conversation:
            reply = routed_offline_reply("Kill the desk lamp", [])
        self.assertEqual(reply, "Desk lamp turned off.")
        control.assert_called_once_with("turn off desk lamp")
        conversation.assert_not_called()

    def test_high_confidence_brightness_route_is_bounded_and_executed(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"kind":"light_brightness","target":"desk lamp",'
            '"brightness_pct":35,"confidence":0.97}'
        )}}]}
        with self._env(), patch("brain.local_model.requests.post", return_value=response):
            route = local_model.classify_semantic_route("Set the desk lamp to 35 percent")
        self.assertEqual(route, {
            "kind": "light_brightness", "target": "desk lamp", "brightness_pct": 35,
        })
        with patch("brain.local_model.classify_semantic_route", return_value=route), patch(
            "brain.intents.set_light_brightness", return_value="Brightness sent."
        ) as brightness:
            self.assertEqual(
                routed_offline_reply("Set the desk lamp to 35 percent", []),
                "Brightness sent.",
            )
        brightness.assert_called_once_with("desk lamp", 35)

    def test_brightness_route_rejects_invalid_or_unrelated_values(self) -> None:
        response = Mock()
        for user_input, content in (
            ("Set desk lamp brighter", '{"kind":"light_brightness","target":"desk lamp","brightness_pct":110,"confidence":0.99}'),
            ("Set a light to 30 percent", '{"kind":"light_brightness","target":"bedroom lamp","brightness_pct":30,"confidence":0.99}'),
        ):
            with self.subTest(user_input=user_input):
                local_model._unavailable_until = 0.0
                response.json.return_value = {"choices": [{"message": {"content": content}}]}
                with self._env(), patch("brain.local_model.requests.post", return_value=response):
                    self.assertIsNone(local_model.classify_semantic_route(user_input))

    def test_named_color_route_is_bounded_and_executed(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"kind":"light_color","target":"desk lamp",'
            '"color":"purple","confidence":0.97}'
        )}}]}
        request = "Make the desk lamp purple"
        with self._env(), patch("brain.local_model.requests.post", return_value=response):
            route = local_model.classify_semantic_route(request)
        self.assertEqual(route, {
            "kind": "light_color", "target": "desk lamp", "color": "purple",
        })
        with patch("brain.local_model.classify_semantic_route", return_value=route), patch(
            "brain.intents.set_light_color", return_value="Color sent."
        ) as color:
            self.assertEqual(routed_offline_reply(request, []), "Color sent.")
        color.assert_called_once_with("desk lamp", "purple")

    def test_named_color_route_rejects_hallucinated_unsupported_and_low_confidence(self) -> None:
        response = Mock()
        cases = (
            ("Make the desk lamp chartreuse", '{"kind":"light_color","target":"desk lamp","color":"chartreuse","confidence":0.99}'),
            ("Make the desk lamp red", '{"kind":"light_color","target":"bedroom lamp","color":"red","confidence":0.99}'),
            ("Make a light red", '{"kind":"light_color","target":"bedroom light","color":"red","confidence":0.99}'),
            ("Maybe make the desk lamp red", '{"kind":"light_color","target":"desk lamp","color":"red","confidence":0.2}'),
            ("Make the desk lamp blue", '{"kind":"light_color","target":"desk lamp","color":"red","confidence":0.99}'),
        )
        for user_input, content in cases:
            with self.subTest(user_input=user_input):
                local_model._unavailable_until = 0.0
                response.json.return_value = {"choices": [{"message": {"content": content}}]}
                with self._env(), patch("brain.local_model.requests.post", return_value=response):
                    self.assertIsNone(local_model.classify_semantic_route(user_input))

    def test_climate_route_is_structured_bounded_and_executed(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"kind":"climate_temperature","target":"main thermostat",'
            '"temperature":72,"unit":"F","confidence":0.98}'
        )}}]}
        request = "Set the main thermostat to 72 degrees Fahrenheit"
        with self._env(), patch("brain.local_model.requests.post", return_value=response):
            route = local_model.classify_semantic_route(request)
        self.assertEqual(route, {
            "kind": "climate_temperature", "target": "main thermostat",
            "temperature": 72.0, "unit": "F",
        })
        with patch("brain.local_model.classify_semantic_route", return_value=route), patch(
            "brain.intents.set_climate_temperature", return_value="Temperature sent."
        ) as climate:
            self.assertEqual(routed_offline_reply(request, []), "Temperature sent.")
        climate.assert_called_once_with("main thermostat", 72.0, "F")

    def test_climate_route_rejects_unsafe_ambiguous_and_low_confidence_output(self) -> None:
        response = Mock()
        cases = (
            ("Set main thermostat to 120 F", '{"kind":"climate_temperature","target":"main thermostat","temperature":120,"unit":"F","confidence":0.99}'),
            ("Set thermostat to 72 F", '{"kind":"climate_temperature","target":"upstairs thermostat","temperature":72,"unit":"F","confidence":0.99}'),
            ("Maybe set main thermostat to 72 F", '{"kind":"climate_temperature","target":"main thermostat","temperature":72,"unit":"F","confidence":0.3}'),
            ("Set main thermostat to 72", '{"kind":"climate_temperature","target":"main thermostat","temperature":72,"unit":"K","confidence":0.99}'),
        )
        for user_input, content in cases:
            with self.subTest(user_input=user_input):
                local_model._unavailable_until = 0.0
                response.json.return_value = {"choices": [{"message": {"content": content}}]}
                with self._env(), patch("brain.local_model.requests.post", return_value=response):
                    self.assertIsNone(local_model.classify_semantic_route(user_input))

    def test_keyword_commands_take_precedence_over_local_model(self) -> None:
        with patch("brain.local_model.generate_reply") as generate:
            reply = routed_offline_reply("what time is it?", [])
        self.assertIn("The time is", reply)
        generate.assert_not_called()

    def test_unknown_conversation_uses_local_then_keyword_fallback(self) -> None:
        with patch("brain.local_model.generate_reply", return_value="Local answer.") as generate:
            self.assertEqual(routed_offline_reply("Tell me a joke", []), "Local answer.")
        generate.assert_called_once()
        with patch("brain.local_model.generate_reply", return_value=None):
            reply = routed_offline_reply("Tell me a joke", [])
        self.assertIn("higher reasoning is offline", reply)

    def test_local_summary_is_opt_in_bounded_and_validated(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "  Key decision   and next step.  "}}]}
        with self._env(LOCAL_MODEL_SUMMARIES="false"), patch(
            "brain.local_model.requests.post"
        ) as post:
            self.assertIsNone(local_model.generate_summary([{"role": "user", "content": "Hi"}]))
        post.assert_not_called()
        local_model._unavailable_until = 0.0
        with self._env(LOCAL_MODEL_SUMMARIES="true"), patch(
            "brain.local_model.requests.post", return_value=response,
        ) as post:
            result = local_model.generate_summary([{"role": "user", "content": "Hi"}] * 20)
        self.assertEqual(result, "Key decision and next step.")
        payload = post.call_args.kwargs["json"]
        self.assertLessEqual(len(payload["messages"][1]["content"]), 5_000)

    def test_agent_persists_local_history_and_reports_backend(self) -> None:
        agent = object.__new__(JarvisAgent)
        agent._client = None
        agent._lock = RLock()
        agent._session_id = "local-session"
        agent._history = []
        agent._recent_tool_context = {}
        agent.last_used_tools = False
        with self._env(), patch(
            "brain.agent.routed_offline_reply", return_value="Local answer."
        ), patch("brain.agent.append_turn") as append:
            self.assertTrue(agent.is_available())
            self.assertEqual(agent.brain_label, "Local model (fallback)")
            self.assertEqual(agent.respond("Hello"), "Local answer.")
        self.assertEqual([item["role"] for item in agent._history], ["user", "assistant"])
        append.assert_called_once_with("Hello", "Local answer.", session_id="local-session")


if __name__ == "__main__":
    unittest.main()
