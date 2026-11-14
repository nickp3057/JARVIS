"""The local Piper voice (TTS_PROVIDER=piper): model loading and caching, both
piper-tts APIs, the room speaker's streamed path with ElevenLabs as fallback,
the TTS cache, and the phone's voice. PiperVoice, aplay and the network are
all faked; nothing plays and no model is loaded."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import types
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from speech import piper_voice, remote_voice, tts
from tests.test_tts import FakeRawPlayer


class FakeVoice12:
    """piper-tts 1.2.x: synthesize_stream_raw yields raw PCM per sentence."""

    def __init__(self, chunks=(b"\x01\x00" * 10, b"\x02\x00" * 10), rate: int = 22050) -> None:  # noqa: ANN001
        self.config = types.SimpleNamespace(sample_rate=rate)
        self.chunks, self.calls = list(chunks), []

    def synthesize_stream_raw(self, text, **kwargs):  # noqa: ANN001, ANN201
        self.calls.append((text, kwargs))
        yield from self.chunks


class FakeVoice13:
    """piper-tts 1.3+: synthesize yields AudioChunk objects."""

    def __init__(self, chunks=(b"\x03\x00" * 8,), rate: int = 22050) -> None:  # noqa: ANN001
        self.config = types.SimpleNamespace(sample_rate=rate)
        self.chunks, self.configs = list(chunks), []

    def synthesize(self, text, syn_config=None):  # noqa: ANN001, ANN201
        self.configs.append(syn_config)
        for chunk in self.chunks:
            yield types.SimpleNamespace(audio_int16_bytes=chunk)


class FakeSynthesisConfig:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = kwargs


def fake_piper_module(voice) -> types.ModuleType:  # noqa: ANN001
    module = types.ModuleType("piper")
    module.PiperVoice = Mock(load=Mock(return_value=voice))  # type: ignore[attr-defined]
    module.SynthesisConfig = FakeSynthesisConfig  # type: ignore[attr-defined]
    return module


class PiperTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.model = self.dir / "jarvis.onnx"
        self.model.write_bytes(b"onnx")
        Path(f"{self.model}.json").write_text("{}")
        patch.dict(os.environ, {"TTS_PROVIDER": "piper", "PIPER_VOICE_MODEL": str(self.model)}).start()
        for knob in ("PIPER_LENGTH_SCALE", "PIPER_NOISE_SCALE", "PIPER_NOISE_W", "PIPER_SPEAKER_ID",
                     "TTS_STREAM_PLAYBACK"):
            os.environ.pop(knob, None)
        patch.object(piper_voice, "_voices", {}).start()
        patch.object(piper_voice, "_warned", set()).start()
        self.addCleanup(patch.stopall)

    def use_voice(self, voice) -> None:  # noqa: ANN001
        """The voice is already loaded (cached), as after the first line."""
        piper_voice._voices[str(self.model)] = voice


class SettingsAndLoadingTests(PiperTestCase):
    def test_provider_is_elevenlabs_unless_set(self) -> None:
        self.assertTrue(piper_voice.selected())
        with patch.dict(os.environ, {"TTS_PROVIDER": " Piper "}):
            self.assertTrue(piper_voice.selected())
        os.environ.pop("TTS_PROVIDER")
        self.assertFalse(piper_voice.selected())

    def test_model_path_forms(self) -> None:
        self.assertEqual(piper_voice.model_path(), self.model)
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": "en_GB-alan-medium"}):
            self.assertEqual(piper_voice.model_path(), piper_voice._VOICE_DIR / "en_GB-alan-medium.onnx")
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": "models/tts/bettany.onnx"}):
            self.assertEqual(piper_voice.model_path(), piper_voice._PROJECT_ROOT / "models/tts/bettany.onnx")
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": ""}):
            self.assertIsNone(piper_voice.model_path())
            self.assertEqual(piper_voice.model_path("en_US-ryan-high"),
                             piper_voice._VOICE_DIR / "en_US-ryan-high.onnx")

    def test_knobs_are_read_live_and_bad_values_ignored(self) -> None:
        self.assertEqual(set(piper_voice.settings().values()), {None})
        with patch.dict(os.environ, {"PIPER_LENGTH_SCALE": "1.1", "PIPER_NOISE_SCALE": "0.5",
                                     "PIPER_NOISE_W": "oops", "PIPER_SPEAKER_ID": "2"}):
            self.assertEqual(piper_voice.settings(), {"speaker_id": 2, "length_scale": 1.1,
                                                      "noise_scale": 0.5, "noise_w": None})

    def test_model_loads_once_and_is_cached_by_path(self) -> None:
        voice = FakeVoice12()
        module = fake_piper_module(voice)
        with patch.dict(sys.modules, {"piper": module}):
            self.assertIs(piper_voice.load(self.model), voice)
            self.assertIs(piper_voice.load(self.model), voice)
        module.PiperVoice.load.assert_called_once_with(str(self.model), config_path=f"{self.model}.json")

    def test_load_failures_are_piper_unavailable(self) -> None:
        with patch.dict(sys.modules, {"piper": None}):  # piper-tts not installed
            with self.assertRaisesRegex(piper_voice.PiperUnavailable, "not installed"):
                piper_voice.load(self.model)
        with self.assertRaisesRegex(piper_voice.PiperUnavailable, "model not found"):
            piper_voice.load(self.dir / "missing.onnx")
        Path(f"{self.model}.json").unlink()
        with self.assertRaisesRegex(piper_voice.PiperUnavailable, "config not found"):
            piper_voice.load(self.model)
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": ""}):
            with self.assertRaises(piper_voice.PiperUnavailable):
                piper_voice.open_stream("Hello.")

    def test_api_12_gets_only_the_knobs_that_are_set(self) -> None:
        voice = FakeVoice12()
        with patch.dict(os.environ, {"PIPER_LENGTH_SCALE": "1.2"}):
            pcm = b"".join(piper_voice.pcm_chunks(voice, "Hello."))
        self.assertEqual(pcm, b"\x01\x00" * 10 + b"\x02\x00" * 10)
        self.assertEqual(voice.calls, [("Hello.", {"length_scale": 1.2})])

    def test_api_13_uses_synthesis_config(self) -> None:
        voice = FakeVoice13()
        with patch.dict(sys.modules, {"piper": fake_piper_module(voice)}), \
                patch.dict(os.environ, {"PIPER_NOISE_W": "0.7", "PIPER_SPEAKER_ID": "1"}):
            pcm = b"".join(piper_voice.pcm_chunks(voice, "Hello."))
        self.assertEqual(pcm, b"\x03\x00" * 8)
        self.assertEqual(voice.configs[0].kwargs, {"speaker_id": 1, "length_scale": None,
                                                   "noise_scale": None, "noise_w_scale": 0.7})

    def test_write_wav_uses_the_models_rate(self) -> None:
        self.use_voice(FakeVoice12(rate=16000))
        out = self.dir / "out.wav"
        self.assertEqual(piper_voice.write_wav("Hello.", out), 16000)
        with wave.open(str(out), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnframes()), (16000, 20))

    def test_resample(self) -> None:
        pcm = b"\x00\x10" * 1600
        self.assertIs(piper_voice.resample_pcm16(pcm, 22050, 22050), pcm)
        self.assertEqual(len(piper_voice.resample_pcm16(pcm, 16000, 22050)), 2205 * 2)

    def test_failures_warn_once(self) -> None:
        with self.assertLogs("jarvis.speech.piper_voice", level="WARNING"):
            piper_voice.warn_once("model not found")
        with self.assertNoLogs("jarvis.speech.piper_voice", level="WARNING"):
            piper_voice.warn_once("model not found")


class RoomSpeakerTests(PiperTestCase):
    """speak() with Piper as the main voice."""

    def setUp(self) -> None:
        super().setUp()
        self.out = self.dir / "jarvis_output.wav"
        patch.object(tts, "_OUTPUT_FILE", self.out).start()
        patch.object(tts, "_CACHE_DIR", self.dir / "cache").start()
        patch.object(tts.sys, "platform", "linux").start()
        patch.object(tts.shutil, "which", return_value="/usr/bin/aplay").start()
        patch.object(tts.time, "sleep").start()
        patch.object(tts, "ELEVENLABS_API_KEY", "key").start()
        patch.object(tts, "ELEVENLABS_VOICE_ID", "voice").start()
        self.eleven_stream = patch.object(tts, "_stream_elevenlabs", return_value=b"\x00\x00").start()
        self.eleven_clip = patch.object(tts, "_synthesize_elevenlabs", return_value=False).start()

    def test_streams_into_aplay_at_the_models_rate(self) -> None:
        self.use_voice(FakeVoice12(rate=16000))
        player = FakeRawPlayer()
        with patch.object(tts.subprocess, "Popen", return_value=player) as popen:
            tts.speak("Good evening, sir.")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-r") + 1], "16000")
        self.assertIn("raw", command)
        self.assertEqual(bytes(player.heard), b"\x01\x00" * 10 + b"\x02\x00" * 10)
        self.assertIsNotNone(tts.last_first_audio_ms)
        self.eleven_stream.assert_not_called()
        self.eleven_clip.assert_not_called()

    def test_interrupt_mid_line_stops_and_is_never_respoken(self) -> None:
        event = threading.Event()

        class SetsEvent(FakeVoice12):
            def synthesize_stream_raw(self, text, **kwargs):  # noqa: ANN001, ANN201
                yield b"\x01\x00" * 10
                event.set()  # barge-in while the second sentence is made
                yield b"\x02\x00" * 10

        self.use_voice(SetsEvent())
        player = FakeRawPlayer()
        with patch.object(tts.subprocess, "Popen", return_value=player):
            self.assertEqual(tts._stream_piper("One. Two.", event), b"")
        self.assertEqual(bytes(player.heard), b"\x01\x00" * 10)
        self.assertIsNone(tts._current_player)

    def test_stop_playback_reaches_the_piper_player(self) -> None:
        seen = []

        class Watches(FakeVoice12):
            def synthesize_stream_raw(self, text, **kwargs):  # noqa: ANN001, ANN201
                yield b"\x01\x00"
                seen.append(tts._current_player)
                yield b"\x02\x00"

        self.use_voice(Watches())
        player = FakeRawPlayer()
        with patch.object(tts.subprocess, "Popen", return_value=player):
            tts._stream_piper("Hi.", None)
        self.assertEqual(seen, [player])

    def test_missing_model_falls_back_to_elevenlabs_and_logs_once(self) -> None:
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": str(self.dir / "missing.onnx")}), \
                patch.object(tts.subprocess, "Popen") as popen:
            with self.assertLogs("jarvis.speech.piper_voice", level="WARNING"):
                tts.speak("Hello.")
            with self.assertNoLogs("jarvis.speech.piper_voice", level="WARNING"):
                tts.speak("Hello again.")
        popen.assert_not_called()
        self.assertEqual(self.eleven_stream.call_count, 2)

    def test_synthesis_error_before_audio_falls_back(self) -> None:
        class Broken(FakeVoice12):
            def synthesize_stream_raw(self, text, **kwargs):  # noqa: ANN001, ANN201
                raise RuntimeError("onnx exploded")
                yield b""  # pragma: no cover

        self.use_voice(Broken())
        with patch.object(tts.subprocess, "Popen") as popen, \
                self.assertLogs("jarvis.speech.piper_voice", level="WARNING"):
            tts.speak("Hello.")
        popen.assert_not_called()
        self.eleven_stream.assert_called_once()

    def test_whole_clip_when_not_streaming_or_for_the_orb(self) -> None:
        self.use_voice(FakeVoice12())
        envelopes = []
        with patch.object(tts, "_play_async_and_wait") as play, \
                patch.object(tts.subprocess, "Popen") as popen:
            tts.speak("Hello.", on_envelope=lambda env, step: envelopes.append(step))
            with patch.dict(os.environ, {"TTS_STREAM_PLAYBACK": "0"}):
                tts.speak("Hello.")
        popen.assert_not_called()  # the orb needs the whole clip first
        self.assertEqual(play.call_count, 2)
        self.assertEqual(envelopes, [1 / 30])
        self.assertTrue(self.out.exists())
        self.eleven_clip.assert_not_called()

    def test_cache_is_per_voice_and_knob_and_skips_fallback_audio(self) -> None:
        piper_key = tts._cache_path("Sleep well, sir.")
        with patch.dict(os.environ, {"PIPER_LENGTH_SCALE": "1.1"}):
            self.assertNotEqual(tts._cache_path("Sleep well, sir."), piper_key)
        with patch.dict(os.environ, {"TTS_PROVIDER": "elevenlabs"}):
            self.assertNotEqual(tts._cache_path("Sleep well, sir."), piper_key)

        self.use_voice(FakeVoice12())
        with patch.object(tts.subprocess, "Popen", return_value=FakeRawPlayer()):
            tts.speak("Sleep well, sir.", cache=True)
        self.assertTrue(piper_key.exists())
        with patch.object(tts, "_play_async_and_wait") as play, \
                patch.object(tts.subprocess, "Popen") as popen:
            tts.speak("Sleep well, sir.", cache=True)
        play.assert_called_once_with(piper_key, None)
        popen.assert_not_called()

        piper_key.unlink()
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": str(self.dir / "missing.onnx")}):
            missing_key = tts._cache_path("Sleep well, sir.")
            tts.speak("Sleep well, sir.", cache=True)
        self.eleven_stream.assert_called_once_with("Sleep well, sir.", None)
        self.assertFalse(missing_key.exists())  # ElevenLabs audio never lands under Piper's key

    def test_elevenlabs_default_is_untouched(self) -> None:
        self.use_voice(FakeVoice12())
        with patch.dict(os.environ, {"TTS_PROVIDER": ""}), \
                patch.object(tts.subprocess, "Popen") as popen:
            tts.speak("Hello.")
        popen.assert_not_called()
        self.eleven_stream.assert_called_once()

    def test_cli_writes_a_wav(self) -> None:
        self.use_voice(FakeVoice12())
        out = self.dir / "say.wav"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(tts._main(["--say", "Good evening, sir.", "--wav", str(out)]), 0)
        self.assertTrue(out.exists())
        self.assertIn("22050 Hz", buffer.getvalue())
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {"PIPER_VOICE_MODEL": str(self.dir / "x.onnx")}):
            self.assertEqual(tts._main(["--say", "Hi.", "--wav", str(out)]), 1)

    def test_cli_speaks_and_reports_first_audio(self) -> None:
        self.use_voice(FakeVoice12())
        buffer = io.StringIO()
        with patch.object(tts.subprocess, "Popen", return_value=FakeRawPlayer()), redirect_stdout(buffer):
            self.assertEqual(tts._main(["--say", "Good evening, sir.", "--provider", "piper"]), 0)
        self.assertIn("First audio after", buffer.getvalue())


class PhoneVoiceTests(PiperTestCase):
    def test_phone_gets_the_piper_voice_at_22khz(self) -> None:
        self.use_voice(FakeVoice12(chunks=(b"\x00\x10" * 1600,), rate=16000))
        with patch.object(remote_voice.requests, "post") as post:
            chunks = list(remote_voice.pcm_stream("Hello.", threading.Event()))
        post.assert_not_called()
        self.assertEqual(sum(len(c) for c in chunks), 2205 * 2)
        self.assertTrue(all(len(c) <= remote_voice.CHUNK_BYTES and len(c) % 2 == 0 for c in chunks))

    def test_phone_falls_back_to_elevenlabs_when_piper_cannot_start(self) -> None:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = iter([b"\x01\x00"])
        with patch.dict(os.environ, {"PIPER_VOICE_MODEL": str(self.dir / "missing.onnx"),
                                     "ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "v"}), \
                patch.object(remote_voice.requests, "post", return_value=response) as post, \
                self.assertLogs("jarvis.speech.piper_voice", level="WARNING"):
            self.assertEqual(list(remote_voice.pcm_stream("Hello.", threading.Event())), [b"\x01\x00"])
        post.assert_called_once()

    def test_phone_mid_sentence_failure_is_not_respoken(self) -> None:
        class Dies(FakeVoice12):
            def synthesize_stream_raw(self, text, **kwargs):  # noqa: ANN001, ANN201
                yield b"\x01\x00"
                raise RuntimeError("onnx exploded")

        self.use_voice(Dies())
        with patch.object(remote_voice.requests, "post") as post:
            with self.assertRaises(RuntimeError):
                list(remote_voice.pcm_stream("Hello.", threading.Event()))
        post.assert_not_called()

    def test_stopped_phone_stream_ends_quietly(self) -> None:
        self.use_voice(FakeVoice12())
        stopped = threading.Event()
        stopped.set()
        self.assertEqual(list(remote_voice.pcm_stream("Hello.", stopped)), [])


if __name__ == "__main__":
    unittest.main()
