"""Reflection after failed turns: the lessons store and the reflector's heuristics."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brain import agent_roster, lessons, reflector
from brain.personality import build_lessons_section
from brain.tools import execute_tool

_DAY = 86400.0


def _fake_client(payload):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=payload)],
        model="claude-haiku-4-5-20251001",
        usage=None,
    )
    return client


class _TmpStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.file_patch = patch.object(lessons, "_FILE", Path(self.tmp.name) / "lessons.json")
        self.file_patch.start()
        self.env = patch.dict(os.environ, {"LESSONS_ENABLED": "true", "LESSONS_MAX": "40",
                                           "LESSONS_IN_CONTEXT": "4", "LESSONS_HALF_LIFE_DAYS": "14"})
        self.env.start()
        reflector.reset()

    def tearDown(self) -> None:
        self.env.stop()
        self.file_patch.stop()
        self.tmp.cleanup()
        reflector.reset()


class LessonStoreTests(_TmpStore):
    def test_learn_list_and_forget(self) -> None:
        self.assertIn("Learned lesson", lessons.learn("Use light.desk_lamp for 'the lamp'.", tool="control_device"))
        self.assertIn("light.desk_lamp", lessons.list_lessons())
        lesson_id = lessons.all_lessons()[0]["id"]
        self.assertIn("Forgot lesson", lessons.forget_lesson(lesson_id))
        self.assertIn("No lessons on file", lessons.list_lessons())
        self.assertIn("No lesson matches", lessons.forget_lesson("nothing"))

    def test_similar_lesson_for_same_tool_reinforces(self) -> None:
        lessons.learn("When Nick says the lamp, use light.desk_lamp.", tool="control_device")
        result = lessons.learn("When Nick says 'the lamp' use light.desk_lamp", tool="control_device")
        self.assertIn("Reinforced", result)
        items = lessons.all_lessons()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["hits"], 2)
        # Same wording but a different tool is its own lesson.
        lessons.learn("When Nick says the lamp, use light.desk_lamp.", tool="scene")
        self.assertEqual(len(lessons.all_lessons()), 2)

    def test_lessons_decay_unless_reinforced(self) -> None:
        now = time.time()
        lessons.learn("Old one-off lesson about Radarr years.", tool="add_movie", now=now - 60 * _DAY)
        lessons.learn("Fresh lesson about the bedroom lights.", tool="control_device", now=now)
        texts = [i["text"] for i in lessons.all_lessons(now)]
        self.assertEqual(texts, ["Fresh lesson about the bedroom lights."])
        # A reinforced lesson outlives its first half-life.
        lessons.learn("Check the thermostat mode first.", tool="climate", now=now - 20 * _DAY)
        lessons.learn("Check the thermostat mode first.", tool="climate", now=now - 1 * _DAY)
        self.assertIn("Check the thermostat mode first.", [i["text"] for i in lessons.all_lessons(now)])

    def test_store_is_capped_keeping_the_strongest(self) -> None:
        with patch.dict(os.environ, {"LESSONS_MAX": "3"}):
            now = time.time()
            for n, word in enumerate(["alpha", "bravo", "charlie", "delta", "echo"]):
                lessons.learn(f"Lesson {word} about tool {n}.", tool=f"tool_{n}", now=now - (5 - n) * _DAY)
            items = lessons.all_lessons(now)
        self.assertEqual(len(items), 3)
        self.assertNotIn("tool_0", [i["tool"] for i in items])

    def test_render_block_prefers_relevant_and_respects_toggle(self) -> None:
        lessons.learn("Search Radarr with the release year when a title has remakes.", tool="add_movie")
        lessons.learn("Say a light failed instead of 'Done' when control_device errors.", tool="control_device")
        with patch.dict(os.environ, {"LESSONS_IN_CONTEXT": "1"}):
            block = lessons.render_block("add the movie Dune")
        self.assertIn("Radarr", block)
        self.assertNotIn("control_device", block)
        section = build_lessons_section(lessons.render_block("turn on the light"))
        self.assertIn("Lessons from your own past mistakes", section)
        self.assertEqual(build_lessons_section(""), "")
        with patch.dict(os.environ, {"LESSONS_ENABLED": "false"}):
            self.assertEqual(lessons.render_block("anything"), "")

    def test_tools_list_and_forget(self) -> None:
        lessons.learn("Read back the timer length.", tool="set_timer")
        self.assertIn("Read back the timer length", execute_tool("list_lessons", {}))
        self.assertIn("Forgot", execute_tool("forget_lesson", {"identifier": "timer length"}))

    def test_reflector_on_agent_map(self) -> None:
        self.assertIn("lessons", [a["id"] for a in agent_roster._AGENTS])


class HeuristicTests(unittest.TestCase):
    def test_corrections(self) -> None:
        for text in ("No, the bedroom lights", "that's wrong", "Not that one", "I said the desk lamp",
                     "that didn't work", "it still isn't on", "You misheard me", "not what I asked for",
                     "wrong room"):
            self.assertTrue(reflector.is_correction(text, "Done."), text)
        for text in ("no thanks", "No problem", "nope, that's all", "turn on the lights", "", "no"):
            self.assertFalse(reflector.is_correction(text, "Done."), text)
        # A "no" answering JARVIS's own question is an answer, not a correction.
        self.assertFalse(reflector.is_correction("No, leave it", "Shall I add it to Radarr?"))

    def test_repeat(self) -> None:
        self.assertTrue(reflector.is_repeat("turn off the bedroom lights", "Turn off the bedroom lights please"))
        self.assertFalse(reflector.is_repeat("turn off the lights", "what's the weather tomorrow"))
        self.assertFalse(reflector.is_repeat("yes", "yes"))

    def test_false_success(self) -> None:
        bad = reflector.Turn("lights off", "Done, sir.", [("control_device", "Tool error: HA unreachable")])
        honest = reflector.Turn("lights off", "I couldn't reach Home Assistant, sir.",
                                [("control_device", "Tool error: HA unreachable")])
        fine = reflector.Turn("lights off", "Done.", [("control_device", "Turned off light.bedroom")])
        self.assertTrue(reflector.claims_success_despite(bad))
        self.assertFalse(reflector.claims_success_despite(honest))
        self.assertFalse(reflector.claims_success_despite(fine))

    def test_detect_respects_window(self) -> None:
        now = time.time()
        old = reflector.Turn("play jazz", "Playing jazz.", at=now - 3600)
        self.assertIsNone(reflector.detect([old], reflector.Turn("no, the other playlist", "", at=now)))
        recent = reflector.Turn("play jazz", "Playing jazz.", at=now - 30)
        signal, bad = reflector.detect([recent], reflector.Turn("no, the other playlist", "", at=now))
        self.assertEqual((signal, bad), ("correction", recent))


class ReflectorTests(_TmpStore):
    def test_correction_writes_a_lesson_about_the_previous_turn(self) -> None:
        client = _fake_client({"lesson": "For 'the lamp' use light.desk_lamp.", "tool": "control_device",
                               "situation": "lamp requests"})
        self.assertIsNone(reflector.observe_turn(client, "s1", "turn on the lamp", "Done.",
                                                 [("control_device", "Turned on light.floor_lamp")],
                                                 background=False))
        client.messages.create.assert_not_called()  # a fine turn costs nothing
        signal = reflector.observe_turn(client, "s1", "No, I said the desk lamp", "Sorry, sir. Done.",
                                        background=False)
        self.assertEqual(signal, "correction")
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("turn on the lamp", prompt)
        self.assertIn("No, I said the desk lamp", prompt)
        items = lessons.all_lessons()
        self.assertEqual([i["text"] for i in items], ["For 'the lamp' use light.desk_lamp."])
        self.assertEqual(items[0]["signal"], "correction")

    def test_false_success_and_empty_lesson(self) -> None:
        client = _fake_client({"lesson": ""})
        signal = reflector.observe_turn(client, "s2", "lights off", "Done.",
                                        [("control_device", "Tool error: HA unreachable")], background=False)
        self.assertEqual(signal, "false_success")
        self.assertEqual(lessons.all_lessons(), [])

    def test_disabled_does_nothing(self) -> None:
        client = _fake_client({"lesson": "x"})
        with patch.dict(os.environ, {"LESSONS_ENABLED": "false"}):
            reflector.observe_turn(client, "s3", "lights off", "Done.",
                                   [("control_device", "Tool error: HA unreachable")], background=False)
        client.messages.create.assert_not_called()

    def test_model_failure_is_swallowed(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("boom")
        reflector.observe_turn(client, "s4", "lights off", "Done.",
                               [("control_device", "Tool error: HA unreachable")], background=False)
        self.assertEqual(lessons.all_lessons(), [])

    def test_sessions_do_not_mix(self) -> None:
        client = _fake_client({"lesson": "x"})
        reflector.observe_turn(client, "a", "play some jazz", "Playing jazz.", background=False)
        self.assertIsNone(reflector.observe_turn(client, "b", "no, the other playlist", "Right.",
                                                 background=False))


if __name__ == "__main__":
    unittest.main()
