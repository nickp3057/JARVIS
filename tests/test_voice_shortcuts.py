"""JARVIS learning voice shortcuts from how it handled Nick's phrases."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from brain import tools, voice_presence, voice_shortcuts
from speech import audio, quick_commands, room_sounds, room_voice, wake

SCENE = [("activate_home_action", {"kind": "scene", "name": "movie", "_jarvis_session_id": "s"})]


class ShortcutStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(voice_shortcuts, "_FILE", Path(tmp.name) / "s.json")
        p.start()
        self.addCleanup(p.stop)

    def test_second_time_the_same_way_it_is_learned(self) -> None:
        self.assertFalse(voice_shortcuts.observe("Make it cosy, Jarvis.", SCENE))
        self.assertIsNone(voice_shortcuts.lookup("make it cosy"))
        self.assertTrue(voice_shortcuts.observe("make it cosy please", SCENE))
        self.assertEqual(voice_shortcuts.lookup("Hey Jarvis, make it cosy!"),
                         ("activate_home_action", {"kind": "scene", "name": "movie"}))  # session id dropped
        self.assertFalse(voice_shortcuts.observe("make it cosy", SCENE))  # already learned

    def test_a_different_answer_starts_the_count_again(self) -> None:
        voice_shortcuts.observe("cosy", SCENE)
        voice_shortcuts.observe("cosy", [("activate_home_action", {"kind": "scene", "name": "study"})])
        self.assertIsNone(voice_shortcuts.lookup("cosy"))

    def test_only_simple_single_actions(self) -> None:
        for tools_used in ([], SCENE + SCENE, [("call_home_service", {"domain": "lock"})],
                           [("add_reminder", {"text": "x"})]):
            for _ in range(3):
                voice_shortcuts.observe("do the thing", tools_used)
            self.assertIsNone(voice_shortcuts.lookup("do the thing"), tools_used)
        long = "please turn the lights to something really nice and warm for the evening"
        voice_shortcuts.observe(long, SCENE)
        self.assertFalse(voice_shortcuts.observe(long, SCENE))

    def test_learn_after_setting(self) -> None:
        with patch.dict(os.environ, {"VOICE_SHORTCUT_AFTER": "1"}):
            self.assertTrue(voice_shortcuts.observe("movie time", SCENE))

    def test_failures_forget_and_listing(self) -> None:
        with patch.dict(os.environ, {"VOICE_SHORTCUT_AFTER": "1"}):
            voice_shortcuts.observe("cosy", SCENE)
            voice_shortcuts.observe("lamp please", [("control_device", {"command": "turn on the lamp"})])
        voice_shortcuts.used("cosy", worked=True)
        self.assertIn('"cosy" -> activate_home_action(kind=scene, name=movie), used 1x', voice_shortcuts.describe())
        voice_shortcuts.used("cosy", worked=False)  # stopped working: Claude handles it next time
        self.assertIsNone(voice_shortcuts.lookup("cosy"))
        self.assertEqual(voice_shortcuts.forget("all"), 1)
        self.assertEqual(voice_shortcuts.describe(), "I haven't learned any voice shortcuts yet, sir.")

    def test_the_tool(self) -> None:
        with patch.dict(os.environ, {"VOICE_SHORTCUT_AFTER": "1"}), patch.object(tools.tool_health, "record"):
            voice_shortcuts.observe("cosy", SCENE)
            self.assertIn("cosy", tools.execute_tool("voice_shortcuts", {"action": "list"}))
            self.assertIn("say which", tools.execute_tool("voice_shortcuts", {"action": "forget"}))
            self.assertIn("don't have", tools.execute_tool("voice_shortcuts", {"action": "forget", "phrase": "x"}))
            self.assertEqual(tools.execute_tool("voice_shortcuts", {"action": "forget", "phrase": "cosy"}),
                             "Forgotten 'cosy', sir.")


class RoomVoiceLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(voice_shortcuts, "_FILE", Path(tmp.name) / "s.json").start()
        patch.object(room_sounds, "play").start()
        patch.object(room_voice.time, "sleep").start()
        patch.object(quick_commands, "run", return_value=None).start()
        self.speak = patch("speech.tts.speak").start()
        self.addCleanup(patch.stopall)

    def voice(self, runtime, said: str) -> room_voice.RoomVoice:  # noqa: ANN001
        final = Mock()
        final.transcribe.return_value = said
        return room_voice.RoomVoice(runtime, detector=Mock(), final=final, live=Mock())

    def test_claude_twice_then_instant(self) -> None:
        runtime = Mock(last_used_tools=True, last_turn_tools=SCENE)
        runtime.respond.return_value = "Done."
        for _ in range(2):
            self.voice(runtime, "Make it cosy.")._handle(np.ones(10, dtype=np.int16))
        self.assertEqual(runtime.respond.call_count, 2)
        runtime.record_event.assert_called_once()  # "Learned a voice shortcut"
        with patch.object(tools, "execute_tool", return_value="Turn On command sent to scene.movie.") as run:
            self.voice(runtime, "make it cosy")._handle(np.ones(10, dtype=np.int16))
        self.assertEqual(runtime.respond.call_count, 2)  # no third model call
        run.assert_called_once_with("activate_home_action", {"kind": "scene", "name": "movie"})
        with patch.object(tools, "execute_tool", return_value="Tool error: HA down"):
            self.voice(runtime, "make it cosy")._handle(np.ones(10, dtype=np.int16))
        self.assertIsNone(voice_shortcuts.lookup("make it cosy"))  # broken shortcut forgotten


class LiveSettingsTests(unittest.TestCase):
    def test_thresholds_are_read_live(self) -> None:
        with patch.dict(os.environ, {"WAKE_THRESHOLD": "0.35", "MIC_RMS_THRESHOLD": "200"}):
            self.assertEqual((wake.wake_threshold(), audio.rms_threshold()), (0.35, 200.0))
        with patch.dict(os.environ, {"WAKE_THRESHOLD": "x", "MIC_RMS_THRESHOLD": "y"}):
            self.assertEqual((wake.wake_threshold(), audio.rms_threshold()),
                             (wake.WAKE_THRESHOLD, audio.RMS_THRESHOLD))

    def test_quieter_voice_counts_once_the_threshold_drops(self) -> None:
        voice = room_voice.RoomVoice(Mock(), detector=Mock(), final=Mock(), live=None)
        soft = np.full(audio.FRAME_SAMPLES, 250, dtype=np.int16)
        quiet = np.zeros(audio.FRAME_SAMPLES, dtype=np.int16)

        class Stream:
            def __init__(self) -> None:
                self.frames = [soft] * 3 + [quiet] * 12

            def read(self, timeout=None):  # noqa: ANN001
                return self.frames.pop(0) if self.frames else quiet

        with patch.dict(os.environ, {"MIC_RMS_THRESHOLD": "350"}):
            self.assertIsNone(voice._capture(Stream(), None, 0.5))
        with patch.dict(os.environ, {"MIC_RMS_THRESHOLD": "200"}):
            self.assertIsNotNone(voice._capture(Stream(), None, 0.5))

    def test_room_voice_knobs_are_editable(self) -> None:
        from brain import settings
        for key in ("WAKE_THRESHOLD", "MIC_RMS_THRESHOLD", "VOICE_SILENCE_END_MS", "VOICE_FOLLOWUP_S",
                    "VOICE_START_TIMEOUT_S", "VOICE_SHORTCUT_AFTER"):
            self.assertIn(key, settings.SETTINGS_SCHEMA)


class AutoCacheTests(unittest.TestCase):
    def test_second_time_a_reply_is_said_it_is_kept(self) -> None:
        from speech import tts
        with tempfile.TemporaryDirectory() as tmp, patch.object(tts, "_CACHE_DIR", Path(tmp)):
            self.assertFalse(tts._said_before("Nothing is playing, sir."))
            self.assertTrue(tts._said_before("nothing is playing, sir."))
            self.assertFalse(tts._said_before("x" * 200))  # long answers aren't counted
            with patch.object(tts, "_COUNTS_MAX", 2):
                tts._said_before("a")
                tts._said_before("b")
                self.assertFalse(tts._said_before("nothing is playing, sir."))  # oldest dropped


if __name__ == "__main__":
    unittest.main()
