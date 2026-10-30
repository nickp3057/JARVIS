"""A tool call written out as text ("[calls control_device ...]") never runs:
it must not reach Nick, must not stay in history, and gets one retry."""
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import brain.agent as agent_mod
from brain.agent import JarvisAgent, _PseudoCallGuard, _turn_messages, stream_round_breaks


class _FakeStream:
    def __init__(self, chunks, final):
        self.text_stream = iter(chunks)
        self._final = final

    def get_final_message(self):
        return self._final


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _rounds():
    fake = '[calls control_device with "set fan to 30 percent"]'
    tool = SimpleNamespace(type="tool_use", id="t1", name="control_device", input={"command": "fan 30%"})
    first = SimpleNamespace(stop_reason="end_turn", content=[_text(fake)], usage=None)
    second = SimpleNamespace(stop_reason="tool_use", content=[tool], usage=None)
    third = SimpleNamespace(stop_reason="end_turn", content=[_text("Fan at thirty, sir.")], usage=None)
    return [
        contextlib.nullcontext(_FakeStream(["[calls control", fake[13:]], first)),
        contextlib.nullcontext(_FakeStream([], second)),
        contextlib.nullcontext(_FakeStream(["Fan at thirty, sir."], third)),
    ]


class PseudoCallTests(unittest.TestCase):
    def test_streaming_hides_fake_call_and_retries_for_real(self) -> None:
        agent = object.__new__(JarvisAgent)
        agent._client = Mock()
        agent._client.messages.stream.side_effect = _rounds()
        agent._session_id = "s"
        agent._recent_tool_context = {}
        agent._last_turn_tools = []
        run_tools = Mock(return_value=[])
        token = stream_round_breaks.set(False)
        try:
            with patch.object(agent_mod, "_model", return_value="claude-haiku-4-5-20251001"), \
                 patch.object(agent_mod, "get_tool_specs", return_value=[]), \
                 patch.object(JarvisAgent, "_prepare_turn", return_value=([], [{"role": "user", "content": "fan 30"}], 0)), \
                 patch.object(JarvisAgent, "_run_tools", run_tools), \
                 patch.object(JarvisAgent, "_save_tool_context"), \
                 patch.object(agent_mod, "_serialize_content_block", side_effect=lambda b: vars(b)):
                gen = agent._run_tool_loop_streaming()
                chunks = []
                while True:
                    try:
                        chunks.append(next(gen))
                    except StopIteration as done:
                        final = done.value
                        break
        finally:
            stream_round_breaks.reset(token)
        self.assertEqual("".join(chunks), "Fan at thirty, sir.")
        self.assertEqual(final, "Fan at thirty, sir.")
        run_tools.assert_called_once()

    def test_guard_passes_ordinary_brackets(self) -> None:
        guard = _PseudoCallGuard()
        out = guard.feed("Fan at [30") + guard.feed("%] now.") + guard.flush()
        self.assertEqual(out, "Fan at [30%] now.")
        self.assertFalse(guard.tripped)

    def test_history_drops_fake_calls_from_replies(self) -> None:
        history = [
            {"role": "user", "content": "approve 5808"},
            {"role": "assistant", "content": "[calls apply_media_fix with fix1]The fix hasn't run yet."},
            {"role": "user", "content": "set fan to 30"},
        ]
        self.assertEqual(_turn_messages(history, "")[1]["content"], "The fix hasn't run yet.")


if __name__ == "__main__":
    unittest.main()
