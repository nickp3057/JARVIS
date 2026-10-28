"""Text-to-speech: ElevenLabs request, Piper fallback, WAV helpers, and
playback that can be stopped mid-sentence on Linux/macOS."""
import os
import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from speech import tts


def write_wav(path: Path, samples: list[int], rate: int = 1000) -> None:
    import struct
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", s) for s in samples))


class FakePlayer:
    """A Popen stand-in that 'plays' for ``ticks`` polls."""

    def __init__(self, ticks: int = 3, code: int = 0) -> None:
        self.ticks, self.code, self.returncode, self.terminated = ticks, code, None, False
        self.stderr = Mock(read=Mock(return_value=b"device busy"))

    def poll(self):  # noqa: ANN201
        if self.terminated:
            self.returncode = -15
        elif self.ticks <= 0:
            self.returncode = self.code
        else:
            self.ticks -= 1
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):  # noqa: ANN001, ANN201
        return self.poll()


class WavHelperTests(unittest.TestCase):
    def test_wrap_duration_and_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            tts._wrap_pcm_as_wav(b"\x00\x10" * 2205, path, 22050)
            self.assertAlmostEqual(tts._wav_duration_seconds(path), 0.1, places=3)
            loud_then_quiet = [8000] * 100 + [800] * 100
            write_wav(path, loud_then_quiet, rate=1000)
            envelope = tts.compute_rms_envelope(path, fps=10)
            self.assertEqual((envelope[0], round(envelope[-1], 2)), (1.0, 0.1))
            self.assertEqual(tts._wav_duration_seconds(Path(tmp) / "missing.wav"), 0.0)
            self.assertEqual(tts.compute_rms_envelope(Path(tmp) / "missing.wav"), [])


class SynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "out.wav"
        patch.object(tts, "_OUTPUT_FILE", self.out).start()
        patch.object(tts, "ELEVENLABS_API_KEY", "key").start()
        patch.object(tts, "ELEVENLABS_VOICE_ID", "voice").start()
        self.addCleanup(patch.stopall)

    def test_elevenlabs_request_and_live_speed(self) -> None:
        ok = Mock(status_code=200, content=b"\x00\x00" * 100)
        with patch.object(tts.requests, "post", return_value=ok) as post, \
                patch.dict(os.environ, {"ELEVENLABS_SPEED": "9"}):
            self.assertTrue(tts._synthesize_elevenlabs("Yes, sir."))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["voice_settings"]["speed"], 1.2)  # clamped, and read at call time
        self.assertEqual(post.call_args.kwargs["headers"]["xi-api-key"], "key")
        self.assertTrue(post.call_args.args[0].endswith("/voice"))
        self.assertTrue(self.out.exists())

    def test_elevenlabs_failures(self) -> None:
        with patch.object(tts.requests, "post", return_value=Mock(status_code=401, text="bad key")), \
                self.assertLogs("jarvis.speech.tts", level="WARNING"):
            self.assertFalse(tts._synthesize_elevenlabs("x"))
        with patch.object(tts.requests, "post", side_effect=requests.Timeout("slow")), \
                self.assertLogs("jarvis.speech.tts", level="WARNING"):
            self.assertFalse(tts._synthesize_elevenlabs("x"))
        with patch.object(tts, "ELEVENLABS_API_KEY", ""):
            self.assertFalse(tts._synthesize_elevenlabs("x"))

    def test_piper_failures_are_logged(self) -> None:
        for error in (FileNotFoundError(), subprocess.CalledProcessError(1, "piper", stderr=b"no voice")):
            with patch.object(tts.subprocess, "run", side_effect=error), \
                    self.assertLogs("jarvis.speech.tts", level="ERROR"):
                self.assertFalse(tts._synthesize_piper("x"))
        with patch.object(tts.subprocess, "run"):
            self.assertTrue(tts._synthesize_piper("x"))

    def test_speak_picks_an_engine_and_plays(self) -> None:
        envelopes = []
        with patch.object(tts, "_synthesize_elevenlabs", return_value=False), \
                patch.object(tts, "_synthesize_piper", side_effect=lambda t: write_wav(self.out, [100] * 50) or True), \
                patch.object(tts, "_play_async_and_wait") as play:
            tts.speak("Hello.", on_envelope=lambda env, step: envelopes.append(step))
        play.assert_called_once_with(self.out, None)
        self.assertEqual(envelopes, [1 / 30])
        with patch.object(tts, "_synthesize_elevenlabs", return_value=False), \
                patch.object(tts, "_synthesize_piper", return_value=False), \
                patch.object(tts, "_play_async_and_wait") as play, \
                self.assertLogs("jarvis.speech.tts", level="ERROR"):
            tts.speak("Hello.")
        play.assert_not_called()
        with patch.object(tts, "_synthesize_elevenlabs") as synth:
            tts.speak("   ")
        synth.assert_not_called()


class PosixPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        patch.object(tts.sys, "platform", "linux").start()
        patch.object(tts.time, "sleep").start()
        patch.dict(os.environ, {"AUDIO_OUTPUT_DEVICE": "plughw:1,0"}).start()
        self.addCleanup(patch.stopall)

    def test_plays_to_the_configured_device(self) -> None:
        player = FakePlayer()
        with patch.object(tts.subprocess, "Popen", return_value=player) as popen:
            self.assertTrue(tts._play_posix_blocking(Path("x.wav")))
        self.assertEqual(popen.call_args.args[0], ["aplay", "-q", "-D", "plughw:1,0", "x.wav"])
        self.assertIsNone(tts._current_player)

    def test_interrupt_stops_it(self) -> None:
        player, stop = FakePlayer(ticks=100), threading.Event()
        stop.set()
        with patch.object(tts.subprocess, "Popen", return_value=player), \
                self.assertLogs("jarvis.speech.tts", level="INFO"):
            self.assertTrue(tts._play_posix_blocking(Path("x.wav"), stop))
        self.assertTrue(player.terminated)

    def test_stop_playback_ends_the_current_sentence(self) -> None:
        player = FakePlayer(ticks=100)
        with patch.object(tts, "_current_player", player):
            tts.stop_playback()
        self.assertTrue(player.terminated)

    def test_errors(self) -> None:
        with patch.object(tts.subprocess, "Popen", return_value=FakePlayer(ticks=0, code=1)), \
                self.assertLogs("jarvis.speech.tts", level="ERROR"):
            self.assertFalse(tts._play_posix_blocking(Path("x.wav")))
        with patch.object(tts.subprocess, "Popen", side_effect=FileNotFoundError), \
                self.assertLogs("jarvis.speech.tts", level="ERROR"):
            self.assertFalse(tts._play_posix_blocking(Path("x.wav")))
        with self.assertLogs("jarvis.speech.tts", level="ERROR"):
            self.assertFalse(tts._play_async_and_wait(Path("/nope/missing.wav"), None))


if __name__ == "__main__":
    unittest.main()
