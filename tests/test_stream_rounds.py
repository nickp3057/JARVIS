"""Companion opt-in: each model round streams as its own segment."""
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import brain.agent as agent_mod
from brain.agent import ROUND_BREAK, JarvisAgent, _turn_messages, stream_round_breaks


class _FakeStream:
    def __init__(self, text, final):
        self.text_stream = iter([text])
        self._final = final

    def get_final_message(self):
        return self._final


def _rounds():
    tool = SimpleNamespace(type="tool_use", id="t1", name="find_movie", input={"title": "Gold"})
    first = SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="text", text="Checking."), tool], usage=None)
    second = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Added.")], usage=None)
    return [contextlib.nullcontext(_FakeStream("Checking.", first)), contextlib.nullcontext(_FakeStream("Added.", second))]


class StreamRoundTests(unittest.TestCase):
    def _run(self, segments: bool) -> tuple[list[str], str]:
        agent = object.__new__(JarvisAgent)
        agent._client = Mock()
        agent._client.messages.stream.side_effect = _rounds()
        agent._session_id = "s"
        agent._recent_tool_context = {}
        agent._last_turn_tools = []
        token = stream_round_breaks.set(segments)
        try:
            with patch.object(agent_mod, "_model", return_value="claude-haiku-4-5-20251001"), \
                 patch.object(agent_mod, "get_tool_specs", return_value=[]), \
                 patch.object(JarvisAgent, "_prepare_turn", return_value=([], [{"role": "user", "content": "hi"}], 0)), \
                 patch.object(JarvisAgent, "_run_tools", return_value=[]), \
                 patch.object(JarvisAgent, "_save_tool_context"), \
                 patch.object(agent_mod, "_serialize_content_block", side_effect=lambda b: vars(b)):
                gen = agent._run_tool_loop_streaming()
                chunks = []
                while True:
                    try:
                        chunks.append(next(gen))
                    except StopIteration as done:
                        return chunks, done.value
        finally:
            stream_round_breaks.reset(token)

    def test_default_joins_rounds_with_a_space(self) -> None:
        chunks, text = self._run(False)
        self.assertEqual("".join(chunks), "Checking. Added.")
        self.assertNotIn(ROUND_BREAK, text)

    def test_companion_gets_and_saves_round_breaks(self) -> None:
        chunks, text = self._run(True)
        self.assertEqual(chunks, ["Checking.", ROUND_BREAK, "Added."])
        self.assertEqual(text, f"Checking.{ROUND_BREAK}Added.")

    def test_marker_never_reaches_the_model(self) -> None:
        history = [{"role": "assistant", "content": f"Checking.{ROUND_BREAK}Added."}, {"role": "user", "content": "thanks"}]
        self.assertEqual(_turn_messages(history, "")[0]["content"], "Checking. Added.")
        self.assertIn(ROUND_BREAK, history[0]["content"])


if __name__ == "__main__":
    unittest.main()
