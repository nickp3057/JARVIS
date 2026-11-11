"""Room voice loop: capturing an utterance, acting on it, and its sounds.

No microphone, models or speaker: a fake stream feeds frames, fake
transcribers return text, and sounds/speech are patched out.
"""
import os
import queue
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from brain import voice_presence, voice_shortcuts
from speech import quick_commands, room_sounds, room_voice
from speech.audio import FRAME_SAMPLES, RMS_THRESHOLD
from speech.wake_words import strip_wake_word

LOUD = np.full(FRAME_SAMPLES, int(RMS_THRESHOLD * 4), dtype=np.int16)
QUIET = np.zeros(FRAME_SAMPLES, dtype=np.int16)


class FakeStream:
    """Hands out queued frames, then silence; remembers a preroll."""

    def __init__(self, frames, preroll=None) -> None:
        self.frames = queue.Queue()
        for frame in frames:
            self.frames.put(frame)
        self.preroll = preroll if preroll is not None else np.zeros(0, dtype=np.int16)

    def read(self, timeout=None):  # noqa: ANN001
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            return QUIET

    def drain(self) -> None:
        pass

    def get_recent_audio(self, seconds):  # noqa: ANN001
        return self.preroll


class FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, audio):  # noqa: ANN001
        self.calls += 1
        return self.text


def make_voice(text: str = "", runtime=None, live=None) -> room_voice.RoomVoice:  # noqa: ANN001
    return room_voice.RoomVoice(runtime or Mock(last_used_tools=False), detector=Mock(),
                                final=FakeTranscriber(text), live=live or FakeTranscriber(""))


class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        env = patch.dict(os.environ, {"VOICE_SILENCE_END_MS": "240", "VOICE_MAX_UTTERANCE_S": "3",
                                      "VOICE_MIN_UTTERANCE_MS": "0"})
        env.start()
        self.addCleanup(env.stop)

    def test_speech_then_silence_is_one_utterance(self) -> None:
        voice = make_voice()
        audio = voice._capture(FakeStream([QUIET, LOUD, LOUD, LOUD, QUIET, QUIET, QUIET, LOUD]), None, 2.0)
        # One quiet frame of lead-in is kept (the soft start of the first word);
        # it stops after 3 quiet frames (240 ms), before the last LOUD.
        self.assertEqual(audio.size, 7 * FRAME_SAMPLES)

    def test_a_short_pause_early_doesnt_end_it(self) -> None:
        with patch.dict(os.environ, {"VOICE_MIN_UTTERANCE_MS": "800"}):
            audio = make_voice()._capture(FakeStream([LOUD, QUIET, QUIET, QUIET, LOUD, LOUD] + [QUIET] * 6), None, 2.0)
        self.assertEqual(audio.size, 10 * FRAME_SAMPLES)  # "turn ... the TV on" stays one command

    def test_speech_level_follows_the_room(self) -> None:
        voice = make_voice()
        with patch.dict(os.environ, {"MIC_RMS_THRESHOLD": "350", "VOICE_MIN_RMS": "90"}):
            self.assertEqual(voice._speech_level(), 350)  # nothing learned yet
            for _ in range(200):
                voice._learn_noise(np.full(FRAME_SAMPLES, 40, dtype=np.int16))
            self.assertAlmostEqual(voice._speech_level(), 100, delta=2)  # 2.5x a quiet room
            voice._learn_noise(np.full(FRAME_SAMPLES, 9000, dtype=np.int16))  # a door slams
            self.assertLess(voice._speech_level(), 110)
            voice._noise = 400.0  # a loud fan: capped at the configured threshold
            self.assertEqual(voice._speech_level(), 350)

    def test_nobody_speaks(self) -> None:
        self.assertIsNone(make_voice()._capture(FakeStream([]), None, 0.5))

    def test_said_before_the_wake_word(self) -> None:
        # "game time, Jarvis": speech in the preroll ahead of the wake word, then nothing.
        preroll = np.concatenate([LOUD] * 10 + [QUIET] * 15)
        audio = make_voice()._capture(FakeStream([]), preroll, 4.0)
        np.testing.assert_array_equal(audio, preroll)

    def test_preroll_that_is_only_the_wake_word_is_dropped(self) -> None:
        preroll = np.concatenate([QUIET] * 14 + [LOUD] * 8)  # the last 0.64 s: "hey jarvis"
        audio = make_voice()._capture(FakeStream([LOUD, LOUD, QUIET, QUIET, QUIET]), preroll, 2.0)
        self.assertEqual(audio.size, 5 * FRAME_SAMPLES)

    def test_live_captions_update_while_listening(self) -> None:
        voice_presence.update("listening")
        live = FakeTranscriber("hey jarvis game")
        voice = make_voice(live=live)
        voice._capture(FakeStream([LOUD] * 14 + [QUIET] * 4), None, 2.0)
        for _ in range(50):  # the caption pass runs on its own thread
            if voice_presence.current()["transcript"]:
                break
            threading_sleep(0.01)
        self.assertEqual(voice_presence.current()["transcript"], "game")
        self.assertGreaterEqual(live.calls, 1)

    def test_late_caption_never_overwrites_the_final_transcript(self) -> None:
        voice_presence.update("thinking", transcript="game time")
        captions = room_voice._LiveCaptions(FakeTranscriber("game"), None)
        captions.busy.acquire()
        captions._run(np.concatenate([LOUD] * 8))
        self.assertEqual(voice_presence.current()["transcript"], "game time")


def threading_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


class HandleTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(voice_shortcuts, "_FILE", Path(tmp.name) / "shortcuts.json").start()
        self.sounds = patch.object(room_sounds, "play").start()
        self.speak = patch("speech.tts.speak").start()
        patch.object(room_voice.time, "sleep").start()
        patch.dict(os.environ, {"STREAMING_TTS": "false"}).start()  # the whole-reply path
        self.addCleanup(patch.stopall)
        self.audio = np.concatenate([LOUD] * 5)

    def test_quick_command_plays_the_jingle_without_words(self) -> None:
        with patch.object(quick_commands, "run", return_value=quick_commands.QuickResult(True, "scene.game_time")):
            follow_up = make_voice("Hey Jarvis, game time.")._handle(self.audio)
        self.assertFalse(follow_up)
        self.sounds.assert_called_once_with("done", wait=True)
        self.speak.assert_not_called()
        self.assertEqual(voice_presence.current()["phase"], "idle")
        self.assertEqual(voice_presence.current()["transcript"], "game time.")

    def test_failed_quick_command_says_so(self) -> None:
        with patch.object(quick_commands, "run", return_value=quick_commands.QuickResult(False, "x", "offline")):
            make_voice("lamp on")._handle(self.audio)
        self.sounds.assert_called_once_with("error", wait=True)
        self.assertEqual(voice_presence.current()["reply"], "That didn't work, sir.")

    def test_done_after_a_tool_becomes_the_jingle(self) -> None:
        runtime = Mock(last_used_tools=True)
        runtime.respond.return_value = "Done."
        with patch.object(quick_commands, "run", return_value=None):
            follow_up = make_voice("make it cosy", runtime)._handle(self.audio)
        runtime.respond.assert_called_once_with("make it cosy", spoken=True)
        self.sounds.assert_called_once_with("done", wait=True)
        self.speak.assert_not_called()
        self.assertFalse(follow_up)

    def test_an_answer_is_spoken_and_keeps_listening(self) -> None:
        runtime = Mock(last_used_tools=False)
        runtime.respond.return_value = "Fifty-four and sunny, sir."
        with patch.object(quick_commands, "run", return_value=None):
            follow_up = make_voice("weather tomorrow", runtime)._handle(self.audio)
        self.speak.assert_called_once_with("Fifty-four and sunny, sir.", cache="auto")
        self.assertTrue(follow_up)
        self.assertEqual(voice_presence.current()["reply"], "Fifty-four and sunny, sir.")

    def test_a_question_after_tools_keeps_listening(self) -> None:
        runtime = Mock(last_used_tools=True)
        runtime.respond.return_value = "Two Golds, sir: 2016 or 2014?"
        with patch.object(quick_commands, "run", return_value=None):
            self.assertTrue(make_voice("add gold", runtime)._handle(self.audio))

    def test_nothing_heard_goes_back_to_idle(self) -> None:
        runtime = Mock()
        self.assertFalse(make_voice("Hey Jarvis.", runtime)._handle(self.audio))
        runtime.respond.assert_not_called()
        self.assertEqual(voice_presence.current()["phase"], "idle")

    def test_a_failed_turn_is_an_error_not_a_crash(self) -> None:
        runtime = Mock(last_used_tools=False)
        runtime.respond.side_effect = RuntimeError("API down")
        with patch.object(quick_commands, "run", return_value=None), \
                self.assertLogs("jarvis.speech.room_voice", level="ERROR"):
            make_voice("what's up", runtime)._handle(self.audio)
        self.sounds.assert_called_once_with("error", wait=True)


class StreamedReplyTests(unittest.TestCase):
    """STREAMING_TTS: a long reply starts after its first sentence."""

    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(voice_shortcuts, "_FILE", Path(tmp.name) / "shortcuts.json").start()
        self.sounds = patch.object(room_sounds, "play").start()
        self.speak = patch("speech.tts.speak").start()
        patch.object(room_voice.time, "sleep").start()
        patch.dict(os.environ, {"STREAMING_TTS": "true"}).start()
        patch.object(quick_commands, "run", return_value=None).start()
        self.addCleanup(patch.stopall)
        self.audio = np.concatenate([LOUD] * 5)

    def runtime(self, fragments, tools=False):  # noqa: ANN001
        runtime = Mock(last_used_tools=tools, brain_online=True)
        runtime.respond_streaming.side_effect = lambda text, spoken: iter(fragments)
        return runtime

    def test_sentences_are_spoken_as_they_arrive(self) -> None:
        runtime = self.runtime(["Fifty-four and ", "sunny, sir. Rain ", "by six. Want an umbrella reminder?"])
        follow_up = make_voice("weather tomorrow", runtime)._handle(self.audio)
        runtime.respond_streaming.assert_called_once_with("weather tomorrow", spoken=True)
        said = [c.args[0] for c in self.speak.call_args_list]
        self.assertEqual(said, ["Fifty-four and sunny, sir.", "Rain by six.", "Want an umbrella reminder?"])
        self.assertTrue(follow_up)
        full = "Fifty-four and sunny, sir. Rain by six. Want an umbrella reminder?"
        self.assertEqual(voice_presence.current()["reply"], full)
        self.assertEqual(voice_presence.current()["phase"], "idle")

    def test_a_bare_done_is_still_the_jingle(self) -> None:
        runtime = self.runtime(["Done."], tools=True)
        self.assertFalse(make_voice("make it cosy", runtime)._handle(self.audio))
        self.sounds.assert_called_once_with("done", wait=True)
        self.speak.assert_not_called()

    def test_a_one_sentence_reply_keeps_the_cache(self) -> None:
        make_voice("hi", self.runtime(["Evening, ", "sir."]))._handle(self.audio)
        self.speak.assert_called_once_with("Evening, sir.", cache="auto")

    def test_a_failed_turn_is_an_error(self) -> None:
        runtime = Mock(last_used_tools=False, brain_online=True)
        runtime.respond_streaming.side_effect = RuntimeError("API down")
        with self.assertLogs("jarvis.speech.room_voice", level="ERROR"):
            self.assertFalse(make_voice("what's up", runtime)._handle(self.audio))
        self.sounds.assert_called_once_with("error", wait=True)

    def test_off_by_default(self) -> None:
        with patch.dict(os.environ, {"STREAMING_TTS": "false"}):
            runtime = Mock(last_used_tools=False, brain_online=True)
            runtime.respond.return_value = "Fine, sir."
            make_voice("hi", runtime)._handle(self.audio)
        runtime.respond_streaming.assert_not_called()


class HushTests(unittest.TestCase):
    """A tap on the iPad ends the exchange: no reply spoken, no follow-up."""

    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        patch.object(room_sounds, "play").start()
        self.speak = patch("speech.tts.speak").start()
        self.stop_playback = patch("speech.tts.stop_playback").start()
        patch.dict(os.environ, {"STREAMING_TTS": "false"}).start()
        self.addCleanup(patch.stopall)
        self.audio = np.concatenate([LOUD] * 5)

    def test_idle_hush_does_nothing(self) -> None:
        voice = make_voice()
        self.assertFalse(voice.hush())
        self.stop_playback.assert_not_called()
        self.assertFalse(voice._hushed.is_set())

    def test_hush_while_thinking_drops_the_reply_and_follow_up(self) -> None:
        runtime = Mock(last_used_tools=False)
        voice = make_voice("what do you think about that", runtime)

        def respond(text, spoken):  # noqa: ANN001
            self.assertTrue(voice.hush())  # tapped while he was thinking
            return "Quite something, sir. Shall I go on?"

        runtime.respond.side_effect = respond
        with patch.object(quick_commands, "run", return_value=None):
            follow_up = voice._handle(self.audio)
        self.assertFalse(follow_up)
        self.speak.assert_not_called()
        self.stop_playback.assert_called()
        self.assertEqual(voice_presence.current()["phase"], "idle")

    def test_hush_closes_a_listening_window(self) -> None:
        voice_presence.update("listening")
        voice = make_voice()
        voice.hush()
        self.assertIsNone(voice._capture(FakeStream([LOUD] * 10), None, start_timeout=1.0))

    def test_module_hush_without_a_room_voice(self) -> None:
        with patch.object(room_voice, "_voice", None):
            self.assertFalse(room_voice.hush())


class SwitchTests(unittest.TestCase):
    def test_enabled_and_start_need_room_voice_on(self) -> None:
        with patch.dict(os.environ, {"ROOM_VOICE": "0"}):
            self.assertFalse(room_voice.enabled())
            self.assertIsNone(room_voice.start(Mock()))
        for value in ("1", "true", "on"):
            with patch.dict(os.environ, {"ROOM_VOICE": value}):
                self.assertTrue(room_voice.enabled())


class SoundTests(unittest.TestCase):
    def test_sounds_render_once_as_short_wavs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(room_sounds, "_DIR", Path(tmp)):
            for name in ("listen", "done", "error"):
                path = room_sounds._render(name)
                with wave.open(str(path)) as wav:
                    self.assertEqual((wav.getnchannels(), wav.getframerate()), (1, room_sounds._RATE))
                    self.assertLess(wav.getnframes() / wav.getframerate(), 0.5)
                mtime = path.stat().st_mtime_ns
                self.assertEqual(room_sounds._render(name).stat().st_mtime_ns, mtime)

    def test_play_uses_the_configured_alsa_device_and_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(room_sounds, "_DIR", Path(tmp)), \
                patch.object(room_sounds.sys, "platform", "linux"), \
                patch.dict(os.environ, {"AUDIO_OUTPUT_DEVICE": "plughw:1,0"}), \
                patch.object(room_sounds.subprocess, "Popen") as popen:
            room_sounds.play("done")
            self.assertEqual(popen.call_args.args[0][:4], ["aplay", "-q", "-D", "plughw:1,0"])
            popen.side_effect = FileNotFoundError("aplay")
            room_sounds.play("done")  # no speaker tool: silent, not an exception


class WakeWordTests(unittest.TestCase):
    def test_wake_word_anywhere(self) -> None:
        for said, left in (("Hey Jarvis, game time.", "game time."), ("Game time, Jarvis.", "Game time"),
                           ("Jarvis turn off the lamp", "turn off the lamp"), ("hi jarvis", ""),
                           ("Jarvis.", ""), ("what time is it", "what time is it"), ("", "")):
            self.assertEqual(strip_wake_word(said), left, said)


if __name__ == "__main__":
    unittest.main()
