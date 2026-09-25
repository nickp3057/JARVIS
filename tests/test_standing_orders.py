"""Standing orders: durable behavioural directives Nick gives JARVIS."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import standing_orders
from brain.personality import build_standing_orders_section


class StandingOrderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "standing_orders.json"
        self.file_patch = patch.object(standing_orders, "_FILE", self.path)
        self.file_patch.start()

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.tmp.cleanup()

    def test_add_list_and_render(self) -> None:
        reply = standing_orders.add_order("  Always give temperatures   in Celsius. ")
        self.assertIn("recorded", reply)
        self.assertIn("Always give temperatures in Celsius.", reply)
        listing = standing_orders.list_orders()
        self.assertIn("Always give temperatures in Celsius.", listing)
        block = standing_orders.render_block()
        self.assertTrue(block.startswith("- Always give temperatures in Celsius. (order "))
        self.assertTrue(self.path.exists())

    def test_empty_store_renders_nothing(self) -> None:
        self.assertEqual(standing_orders.render_block(), "")
        self.assertEqual(build_standing_orders_section(""), "")
        self.assertIn("No standing orders", standing_orders.list_orders())

    def test_duplicate_is_not_added_twice(self) -> None:
        standing_orders.add_order("Stop saying certainly.")
        reply = standing_orders.add_order("stop saying CERTAINLY.")
        self.assertIn("already", reply)
        self.assertEqual(len(standing_orders.all_orders()), 1)

    def test_remove_by_id_and_by_fragment(self) -> None:
        first = standing_orders.add_order("Stop saying certainly.")
        standing_orders.add_order("Never suggest horror films.")
        order_id = first.split("Standing order ")[1].split(" ")[0]
        self.assertIn("removed", standing_orders.remove_order(order_id))
        self.assertIn("removed", standing_orders.remove_order("horror"))
        self.assertEqual(standing_orders.all_orders(), [])

    def test_ambiguous_fragment_asks_for_id(self) -> None:
        standing_orders.add_order("Keep film suggestions to one line.")
        standing_orders.add_order("Never suggest horror films.")
        reply = standing_orders.remove_order("film")
        self.assertIn("Several", reply)
        self.assertEqual(len(standing_orders.all_orders()), 2)

    def test_unknown_identifier_is_reported(self) -> None:
        self.assertIn("No standing order matches", standing_orders.remove_order("nope"))
        self.assertIn("needs some wording", standing_orders.add_order("   "))

    def test_cap_is_enforced(self) -> None:
        with patch.object(standing_orders, "MAX_ORDERS", 2):
            standing_orders.add_order("One.")
            standing_orders.add_order("Two.")
            self.assertIn("capped", standing_orders.add_order("Three."))

    def test_corrupt_file_degrades_to_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(standing_orders.all_orders(), [])
        self.assertIn("recorded", standing_orders.add_order("Recover."))

    def test_prompt_section_wraps_orders(self) -> None:
        section = build_standing_orders_section("- Be blunt. (order abc)")
        self.assertIn("# Standing orders from Nick", section)
        self.assertIn("- Be blunt. (order abc)", section)


class StandingOrdersReachThePromptTests(unittest.TestCase):
    def test_agent_injects_orders_into_dynamic_block(self) -> None:
        from brain.agent import JarvisAgent

        agent = object.__new__(JarvisAgent)
        agent._recent_tool_context = {}
        agent._session_id = "s"
        with patch.object(standing_orders, "render_block", return_value="- Be blunt. (order abc)"), \
             patch("brain.agent.render_memory_block", return_value=""), \
             patch("brain.agent.render_habits_block", return_value=""):
            blocks = agent._system_blocks()
        self.assertEqual(len(blocks), 2)
        self.assertIn("cache_control", blocks[0])
        self.assertIn("Standing orders from Nick", blocks[1]["text"])
        self.assertIn("Be blunt.", blocks[1]["text"])
        self.assertNotIn("Be blunt.", blocks[0]["text"])


if __name__ == "__main__":
    unittest.main()
