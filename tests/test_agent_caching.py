import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from brain import agent as agent_mod
from brain.agent import JarvisAgent, _turn_messages, _with_cache_breakpoints


class TurnCachingTests(unittest.TestCase):
    HISTORY = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Good evening, sir."},
        {"role": "user", "content": "weather?"},
    ]

    def test_turn_context_rides_on_newest_user_message_only(self):
        messages = _turn_messages(self.HISTORY, "\n\n# What you remember\n- likes tea")
        self.assertEqual(messages[:2], self.HISTORY[:2])
        self.assertEqual(messages[-1]["content"][0], {"type": "text", "text": "weather?"})
        self.assertIn("likes tea", messages[-1]["content"][1]["text"])
        self.assertEqual(self.HISTORY[-1]["content"], "weather?")  # stored history untouched

    def test_breakpoints_on_anchor_and_tail_without_mutating_input(self):
        messages = _turn_messages(self.HISTORY, "ctx")
        marked = _with_cache_breakpoints(messages, anchor=1)
        self.assertIn("cache_control", marked[1]["content"][-1])
        self.assertIn("cache_control", marked[2]["content"][-1])
        self.assertNotIn("cache_control", marked[0]["content"][-1])
        self.assertNotIn("cache_control", messages[2]["content"][-1])
        self.assertEqual(messages[1]["content"], "Good evening, sir.")

    def test_next_turn_sends_previous_messages_byte_identical(self):
        # What turn N marked as its anchor must reappear unchanged in turn N+1.
        turn_n = _with_cache_breakpoints(_turn_messages(self.HISTORY, "ctx A"), anchor=1)
        later = self.HISTORY + [{"role": "assistant", "content": "Sunny."}, {"role": "user", "content": "thanks"}]
        turn_n1 = _with_cache_breakpoints(_turn_messages(later, "ctx B"), anchor=3)

        def unmarked(messages):
            return [[{k: v for k, v in b.items() if k != "cache_control"} for b in m["content"]] for m in messages]

        self.assertEqual(unmarked(turn_n[:2]), unmarked(turn_n1[:2]))

    def test_system_prompt_has_no_per_turn_content(self):
        agent = object.__new__(JarvisAgent)
        agent._recent_tool_context = {"s": '[{"tool": "x"}]'}
        agent._session_id = "s"
        with patch.object(agent_mod.standing_orders, "render_block", return_value=""), \
             patch("brain.agent.render_memory_block", return_value="- remembers tea"):
            blocks = agent._system_blocks()
            context = agent._turn_context("tea?")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertIn("remembers tea", context)
        self.assertIn("Recent trusted tool results", context)


class ParallelToolTests(unittest.TestCase):
    def test_multiple_tool_calls_run_concurrently_and_keep_order(self):
        agent = object.__new__(JarvisAgent)
        agent._session_id = "s"
        barrier = threading.Barrier(2, timeout=2)

        def fake_execute(name, tool_input, session_id=None):
            barrier.wait()  # deadlocks (times out) if run one after another
            return f"{name} ok"

        uses = [SimpleNamespace(name="a", input={}, id="1"), SimpleNamespace(name="b", input={}, id="2")]
        context = []
        with patch.object(agent_mod, "execute_tool", side_effect=fake_execute):
            results = agent._run_tools(uses, context)
        self.assertEqual([r["tool_use_id"] for r in results], ["1", "2"])
        self.assertEqual([r["content"] for r in results], ["a ok", "b ok"])
        self.assertEqual([c["tool"] for c in context], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
