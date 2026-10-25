"""Conversation model follows new releases; generation-specific request settings."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx

import brain.agent as agent_mod
from brain import model_select
from brain.agent import JarvisAgent


def _models(*ids_dates):
    return [SimpleNamespace(id=i, created_at=d) for i, d in ids_dates]


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = patch.object(model_select, "_CACHE_FILE", Path(tmp.name) / "model_cache.json")
        cache.start()
        self.addCleanup(cache.stop)
        env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_MODEL_FAMILY": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_pinned_model_is_untouched(self) -> None:
        with patch.object(model_select, "_newest_from_api") as api:
            self.assertEqual(model_select.resolve("claude-haiku-4-5-20251001", "d"), "claude-haiku-4-5-20251001")
        api.assert_not_called()

    def test_auto_picks_newest_haiku_and_caches_a_day(self) -> None:
        listing = _models(("claude-haiku-4-5-20251001", "2025-10-01"), ("claude-haiku-5-5", "2026-09-01"),
                          ("claude-opus-5-5", "2026-10-01"))
        client = Mock()
        client.models.list.return_value = listing
        with patch("anthropic.Anthropic", return_value=client), \
             patch.object(model_select.time, "time", side_effect=[1000, 2000, 1000 + 25 * 3600]):
            self.assertEqual(model_select.resolve("", "d"), "claude-haiku-5-5")
            self.assertEqual(model_select.resolve("auto", "d"), "claude-haiku-5-5")
            self.assertEqual(client.models.list.call_count, 1)
            model_select.resolve("auto", "d")
            self.assertEqual(client.models.list.call_count, 2)

    def test_lookup_failure_keeps_default(self) -> None:
        with patch.object(model_select, "_newest_from_api", side_effect=RuntimeError("down")):
            self.assertEqual(model_select.resolve("auto:haiku", "claude-haiku-4-5-20251001"), "claude-haiku-4-5-20251001")

    def test_family_override(self) -> None:
        with patch.object(model_select, "_newest_from_api", return_value="claude-sonnet-5-5") as api:
            self.assertEqual(model_select.resolve("auto:sonnet", "d"), "claude-sonnet-5-5")
        api.assert_called_once_with("sonnet")


class RequestKwargsTests(unittest.TestCase):
    def setUp(self) -> None:
        model_select._plain_models.clear()

    def test_legacy_models_get_plain_requests(self) -> None:
        self.assertEqual(model_select.request_kwargs("claude-haiku-4-5-20251001"), {})

    def test_haiku_5_gets_low_effort_and_no_thinking(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_EFFORT": "", "ANTHROPIC_THINKING": ""}):
            os.environ.pop("ANTHROPIC_EFFORT"); os.environ.pop("ANTHROPIC_THINKING")
            self.assertEqual(model_select.request_kwargs("claude-haiku-5-5"), {
                "extra_body": {"output_config": {"effort": "low"}, "thinking": {"type": "disabled"}}})

    def test_non_haiku_never_gets_disabled_thinking(self) -> None:
        kwargs = model_select.request_kwargs("claude-sonnet-5-5")
        self.assertNotIn("thinking", kwargs["extra_body"])


class AgentFallbackTests(unittest.TestCase):
    def _agent(self, client):
        agent = object.__new__(JarvisAgent)
        agent._client = client
        agent._session_id = "s"
        agent._history = [{"role": "user", "content": "hi"}]
        agent._recent_tool_context = {}
        agent._last_turn_tools = []
        return agent

    def setUp(self) -> None:
        model_select._plain_models.clear()
        patcher = patch.object(agent_mod, "_model", return_value="claude-haiku-6-0")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _bad_request(self):
        response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        return anthropic.BadRequestError("thinking: type disabled is not supported", response=response, body=None)

    def test_rejected_settings_retry_plain_once_and_are_remembered(self) -> None:
        reply = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Evening, sir.")], usage=None)
        client = Mock()
        client.messages.create.side_effect = [self._bad_request(), reply, reply]
        agent = self._agent(client)
        with patch.object(agent_mod, "get_tool_specs", return_value=[]), \
             patch.object(JarvisAgent, "_prepare_turn", return_value=([], [{"role": "user", "content": "hi"}], 0)), \
             patch.object(JarvisAgent, "_save_tool_context"):
            self.assertEqual(agent._run_tool_loop(), "Evening, sir.")
            agent._run_tool_loop()
        calls = client.messages.create.call_args_list
        self.assertIn("extra_body", calls[0].kwargs)
        self.assertNotIn("extra_body", calls[1].kwargs)
        self.assertNotIn("extra_body", calls[2].kwargs)

    def test_refusal_gets_a_spoken_reply(self) -> None:
        client = Mock()
        client.messages.create.return_value = SimpleNamespace(stop_reason="refusal", content=[], usage=None)
        agent = self._agent(client)
        with patch.object(agent_mod, "get_tool_specs", return_value=[]), \
             patch.object(JarvisAgent, "_prepare_turn", return_value=([], [{"role": "user", "content": "hi"}], 0)):
            self.assertEqual(agent._run_tool_loop(), agent_mod._REFUSAL_REPLY)


if __name__ == "__main__":
    unittest.main()
