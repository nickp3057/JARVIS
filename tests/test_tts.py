"""Text-to-speech: ElevenLabs request, Piper fallback, WAV helpers, and
playback that can be stopped mid-sentence on Linux/macOS."""
import os
import subprocess
import tempfile
import threading
import time
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
        with patch.object(tts.cloud_http, "post", return_value=ok) as post, \
                patch.dict(os.environ, {"ELEVENLABS_SPEED": "9"}):
            self.assertTrue(tts._synthesize_elevenlabs("Yes, sir."))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["voice_settings"]["speed"], 1.2)  # clamped, and read at call time
        self.assertEqual(post.call_args.kwargs["headers"]["xi-api-key"], "key")
        self.assertTrue(post.call_args.args[0].endswith("/voice"))
        self.assertTrue(self.out.exists())

    def test_elevenlabs_failures(self) -> None:
        with patch.object(tts.cloud_http, "post", return_value=Mock(status_code=401, text="bad key")), \
                self.assertLogs("jarvis.speech.tts", level="WARNING"):
            self.assertFalse(tts._synthesize_elevenlabs("x"))
        with patch.object(tts.cloud_http, "post", side_effect=requests.Timeout("slow")), \
                self.assertLogs("jarvis.speech.tts", level="WARNING"):
            self.assertFalse(tts._synthesize_elevenlabs("x"))
        with patch.object(tts, "ELEVENLABS_API_KEY", ""):
            self.assertFalse(tts._synthesize_elevenlabs("x"))

    def test_piper_failures_are_logged(self) -> None:
        # The in-process voice is out (no piper-tts / no model), so the piper CLI runs.
        patch.object(tts.piper_voice, "write_wav",
                     side_effect=tts.piper_voice.PiperUnavailable("not installed")).start()
        for error in (FileNotFoundError(), subprocess.CalledProcessError(1, "piper", stderr=b"no voice")):
            with patch.object(tts.subprocess, "run", side_effect=error), \
                    self.assertLogs("jarvis.speech.tts", level="ERROR"):
                self.assertFalse(tts._synthesize_piper("x"))
        with patch.object(tts.subprocess, "run"):
            self.assertTrue(tts._synthesize_piper("x"))

    def test_speak_picks_an_engine_and_plays(self) -> None:
        envelopes = []
        patch.object(tts, "_stream_playback_enabled", return_value=False).start()
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


class FakeStream:
    """An ElevenLabs /stream response: ``chunks`` of PCM, then the end."""

    def __init__(self, chunks, status: int = 200, fail_after: int | None = None) -> None:  # noqa: ANN001
        self.chunks, self.status_code, self.fail_after, self.text, self.closed = chunks, status, fail_after, "bad", False

    def iter_content(self, chunk_size=None):  # noqa: ANN001, ANN201
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index >= self.fail_after:
                raise requests.ConnectionError("dropped")
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeRawPlayer(FakePlayer):
    """aplay reading raw PCM on stdin; ``dies_after`` writes it goes away."""

    def __init__(self, ticks: int = 2, code: int = 0, dies_after: int | None = None) -> None:
        super().__init__(ticks, code)
        self.heard, self.dies_after = bytearray(), dies_after
        self.stdin = Mock(write=self._write, close=Mock())

    def poll(self):  # noqa: ANN201
        if self.code == 0 and not self.terminated and not self.stdin.close.called:
            return None  # a healthy aplay plays on for as long as stdin is open
        return super().poll()

    def _write(self, chunk: bytes) -> None:
        if self.dies_after is not None and len(self.heard) >= self.dies_after:
            self.terminated = True  # stop_playback's terminate() landed mid-sentence
            raise BrokenPipeError
        self.heard += chunk


class StreamedPlaybackTests(unittest.TestCase):
    """ElevenLabs audio goes into aplay as it downloads (Linux)."""

    def setUp(self) -> None:
        patch.object(tts, "ELEVENLABS_API_KEY", "key").start()
        patch.object(tts, "ELEVENLABS_VOICE_ID", "voice").start()
        patch.object(tts.sys, "platform", "linux").start()
        patch.object(tts.shutil, "which", return_value="/usr/bin/aplay").start()
        patch.object(tts.time, "sleep").start()
        patch.dict(os.environ, {"AUDIO_OUTPUT_DEVICE": "plughw:1,0"}).start()
        os.environ.pop("TTS_STREAM_PLAYBACK", None)
        self.addCleanup(patch.stopall)

    def stream(self, response, player, event=None):  # noqa: ANN001, ANN201
        with patch.object(tts.cloud_http, "post", return_value=response) as post, \
                patch.object(tts.subprocess, "Popen", return_value=player) as popen:
            result = tts._stream_elevenlabs("Yes, sir.", event)
        return result, post, popen

    def test_audio_reaches_aplay_as_it_arrives(self) -> None:
        response, player = FakeStream([b"ab", b"", b"cd", b"ef"]), FakeRawPlayer()
        played, post, popen = self.stream(response, player)
        self.assertEqual((played, bytes(player.heard)), (b"abcdef", b"abcdef"))
        self.assertTrue(post.call_args.args[0].endswith("/voice/stream"))
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual(popen.call_args.args[0],
                         ["aplay", "-q", "-D", "plughw:1,0", "-t", "raw", "-f", "S16_LE", "-r", "22050", "-c", "1", "-"])
        player.stdin.close.assert_called_once()
        self.assertTrue(response.closed)
        self.assertIsNone(tts._current_player)

    def test_nothing_heard_means_fall_back(self) -> None:
        with self.assertLogs("jarvis.speech.tts", level="WARNING"):
            self.assertIsNone(self.stream(FakeStream([], status=401), FakeRawPlayer())[0])
            with patch.object(tts.cloud_http, "post", side_effect=requests.Timeout("slow")):
                self.assertIsNone(tts._stream_elevenlabs("x", None))
            # aplay could not open the speaker: say so and let the whole-clip path try.
            self.assertIsNone(self.stream(FakeStream([b"ab"]), FakeRawPlayer(ticks=0, code=1))[0])
        self.assertIsNone(self.stream(FakeStream([]), FakeRawPlayer())[0])  # an empty reply
        with patch.object(tts.cloud_http, "post", return_value=FakeStream([b"ab"])), \
                patch.object(tts.subprocess, "Popen", side_effect=FileNotFoundError):
            self.assertIsNone(tts._stream_elevenlabs("x", None))

    def test_a_stop_is_never_spoken_again(self) -> None:
        # stop_playback mid-sentence, an interrupt event, and a hush before the first sound.
        cut = self.stream(FakeStream([b"ab", b"cd", b"ef"]), FakeRawPlayer(dies_after=2))[0]
        self.assertEqual(cut, b"")
        event = threading.Event()
        event.set()
        player = FakeRawPlayer(ticks=100)
        with self.assertLogs("jarvis.speech.tts", level="INFO"):
            self.assertEqual(self.stream(FakeStream([b"ab", b"cd"]), player, event)[0], b"")
        self.assertTrue(player.terminated)
        self.assertEqual(bytes(player.heard), b"")
        self.assertEqual(self.stream(FakeStream([b"ab"]), FakeRawPlayer(dies_after=0))[0], b"")

    def test_stop_playback_unblocks_a_real_full_pipe(self) -> None:
        # A real process that never reads: the pipe fills and the write blocks,
        # exactly where a hush or barge-in lands mid-sentence.
        patch.stopall()
        patch.object(tts, "ELEVENLABS_API_KEY", "key").start()
        patch.object(tts, "ELEVENLABS_VOICE_ID", "voice").start()
        real_popen = subprocess.Popen
        result: list = []
        with patch.object(tts.cloud_http, "post", return_value=FakeStream([b"\x00" * 4096] * 400)), \
                patch.object(tts.subprocess, "Popen",
                             side_effect=lambda args, **kw: real_popen(["sleep", "30"], **kw)):
            worker = threading.Thread(target=lambda: result.append(tts._stream_elevenlabs("A long line.", None)))
            worker.start()
            deadline = time.monotonic() + 5
            while tts._current_player is None and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)  # let the pipe fill
            tts.stop_playback()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [b""])  # stopped, and not to be spoken again
        self.assertIsNone(tts._current_player)

    def test_a_dropped_download_keeps_what_played(self) -> None:
        player = FakeRawPlayer()
        with self.assertLogs("jarvis.speech.tts", level="WARNING"):
            played = self.stream(FakeStream([b"ab", b"cd"], fail_after=1), player)[0]
        self.assertEqual((played, bytes(player.heard)), (b"", b"ab"))  # heard, but not worth caching

    def test_speak_streams_caches_and_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tts, "_CACHE_DIR", Path(tmp) / "cache"), \
                    patch.object(tts, "_stream_elevenlabs", return_value=b"\x00\x01" * 50) as stream, \
                    patch.object(tts, "_synthesize_elevenlabs") as synth, \
                    patch.object(tts, "_play_async_and_wait") as play:
                tts.speak("Sleep well, sir.", cache=True)
                synth.assert_not_called()
                play.assert_not_called()
                cached = tts._cache_path("Sleep well, sir.")
                self.assertAlmostEqual(tts._wav_duration_seconds(cached), 50 / 22050, places=4)
                tts.speak("Sleep well, sir.", cache=True)  # replayed from the cache
                self.assertEqual((stream.call_count, play.call_args.args[0]), (1, cached))
                stream.return_value = b""  # cut short: nothing kept
                tts.speak("Half a line", cache=True)
                self.assertFalse(tts._cache_path("Half a line").exists())
            with patch.object(tts, "_stream_elevenlabs", return_value=None), \
                    patch.object(tts, "_synthesize_elevenlabs", return_value=True) as synth, \
                    patch.object(tts, "_play_async_and_wait") as play:
                tts.speak("Hello.")
            synth.assert_called_once_with("Hello.")
            play.assert_called_once()

    def test_when_streaming_is_off(self) -> None:
        self.assertTrue(tts._stream_playback_enabled())
        with patch.dict(os.environ, {"TTS_STREAM_PLAYBACK": "0"}):
            self.assertFalse(tts._stream_playback_enabled())
        with patch.object(tts.sys, "platform", "darwin"):
            self.assertFalse(tts._stream_playback_enabled())
        with patch.object(tts, "ELEVENLABS_API_KEY", ""):
            self.assertFalse(tts._stream_playback_enabled())
        # The GUI's audio-reactive orb needs the whole clip before it starts.
        with patch.object(tts, "_stream_elevenlabs") as stream, \
                patch.object(tts, "_synthesize_elevenlabs", return_value=False), \
                patch.object(tts, "_synthesize_piper", return_value=False), \
                self.assertLogs("jarvis.speech.tts", level="ERROR"):
            tts.speak("Hello.", on_envelope=lambda env, step: None)
        stream.assert_not_called()


class CacheTests(unittest.TestCase):
    def test_routine_lines_are_synthesized_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            with patch.object(tts, "_OUTPUT_FILE", out), patch.object(tts, "_CACHE_DIR", Path(tmp) / "cache"), \
                    patch.object(tts, "_stream_playback_enabled", return_value=False), \
                    patch.object(tts, "_synthesize_elevenlabs",
                                 side_effect=lambda text: write_wav(out, [10] * 20) or True) as synth, \
                    patch.object(tts, "_play_async_and_wait") as play:
                tts.speak("Sleep well, sir.", cache=True)
                tts.speak("Sleep well, sir.", cache=True)
                tts.speak("Sleep well, sir.")  # uncached call: synthesizes as before
                self.assertEqual(synth.call_count, 2)
                cached = tts._cache_path("Sleep well, sir.")
                self.assertEqual(play.call_args_list[1].args[0], cached)
                with patch.dict(os.environ, {"ELEVENLABS_SPEED": "1.1"}):
                    self.assertNotEqual(tts._cache_path("Sleep well, sir."), cached)  # new speed, new audio

    def test_the_cache_stays_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            write_wav(out, [1] * 10)
            with patch.object(tts, "_OUTPUT_FILE", out), patch.object(tts, "_CACHE_MAX_FILES", 3):
                for i in range(5):
                    tts._store_in_cache(Path(tmp) / "cache" / f"{i}.wav")
            self.assertEqual(len(list((Path(tmp) / "cache").glob("*.wav"))), 3)


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


class LiveElevenLabsSettingsTests(unittest.TestCase):
    def test_key_voice_and_model_follow_env_edits(self) -> None:
        env = {"ELEVENLABS_API_KEY": "", "ELEVENLABS_VOICE_ID": "", "ELEVENLABS_MODEL": ""}
        with patch.dict(os.environ, env):
            self.assertFalse(tts._elevenlabs_configured())
            # update_setting writes .env and os.environ; no restart needed.
            os.environ.update(ELEVENLABS_API_KEY="k2", ELEVENLABS_VOICE_ID="v2", ELEVENLABS_MODEL="eleven_v3")
            self.assertTrue(tts._elevenlabs_configured())
            url, request = tts._elevenlabs_request("Good evening.")
        self.assertTrue(url.endswith("/v2"))
        self.assertEqual(request["headers"]["xi-api-key"], "k2")
        self.assertEqual(request["json"]["model_id"], "eleven_v3")

    def test_garbled_speed_falls_back(self) -> None:
        with patch.dict(os.environ, {"ELEVENLABS_SPEED": "fast"}):
            self.assertEqual(tts._eleven_speed(), tts.ELEVENLABS_SPEED)


if __name__ == "__main__":
    unittest.main()
