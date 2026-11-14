import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from speech import stt


class DecodeOptionTests(unittest.TestCase):
    def test_default_is_single_deterministic_pass(self):
        with patch.dict(os.environ, {"WHISPER_TEMPERATURE_FALLBACK": ""}):
            options = stt._decode_options()
        self.assertEqual(options["temperature"], 0.0)
        self.assertTrue(options["without_timestamps"])
        self.assertFalse(options["condition_on_previous_text"])

    def test_fallback_flag_restores_library_schedule(self):
        with patch.dict(os.environ, {"WHISPER_TEMPERATURE_FALLBACK": "true"}):
            self.assertNotIn("temperature", stt._decode_options())

    def test_transcribe_passes_options_and_survives_decode_errors(self):
        transcriber = stt.Transcriber()
        segment = MagicMock(text=" hello ")
        transcriber._model = MagicMock()
        transcriber._model.transcribe.return_value = (iter([segment]), None)
        self.assertEqual(transcriber.transcribe(np.zeros(16000, dtype=np.float32)), "hello")
        self.assertEqual(transcriber._model.transcribe.call_args.kwargs["temperature"], 0.0)

        def exploding():
            raise RuntimeError("decode failed")
            yield  # pragma: no cover

        transcriber._model.transcribe.return_value = (exploding(), None)
        self.assertEqual(transcriber.transcribe(np.zeros(16000, dtype=np.float32)), "")


class PromptTests(unittest.TestCase):
    def test_room_words_steer_whisper_and_can_be_overridden(self) -> None:
        import os
        from unittest.mock import patch
        from speech.stt import Transcriber
        t = Transcriber("tiny.en")
        self.assertIsNone(t._initial_prompt())
        t.prompt = "Hey Jarvis, Desk Lights."
        self.assertEqual(t._initial_prompt(), "Hey Jarvis, Desk Lights.")
        with patch.dict(os.environ, {"WHISPER_PROMPT": "off"}):
            self.assertIsNone(t._initial_prompt())
        with patch.dict(os.environ, {"WHISPER_PROMPT": "Lamp."}):
            self.assertEqual(t._initial_prompt(), "Lamp.")


class LiveSettingsTests(unittest.TestCase):
    def test_whisper_settings_are_read_live_and_model_reloads_by_name(self):
        loaded = []

        class FakeWhisper:
            def __init__(self, size, device, compute_type):
                loaded.append((size, device, compute_type))

        fake_module = MagicMock(WhisperModel=FakeWhisper)
        transcriber = stt.Transcriber()
        env = {"WHISPER_MODEL": "small.en", "WHISPER_DEVICE": "cpu", "WHISPER_COMPUTE_TYPE": "int8"}
        with patch.dict("sys.modules", {"faster_whisper": fake_module}), patch.dict(os.environ, env):
            transcriber._ensure_loaded()
            transcriber._ensure_loaded()  # same settings: no reload
            self.assertEqual(loaded, [("small.en", "cpu", "int8")])
            with patch.dict(os.environ, {"WHISPER_MODEL": "base.en"}):
                transcriber._ensure_loaded()
            self.assertEqual(loaded[-1], ("base.en", "cpu", "int8"))
            live = stt.Transcriber("tiny.en")  # the caption model keeps its size
            live._ensure_loaded()
            self.assertEqual(loaded[-1][0], "tiny.en")

    def test_beam_size_tolerates_bad_values(self):
        with patch.dict(os.environ, {"WHISPER_BEAM_SIZE": "five"}):
            self.assertEqual(stt._whisper_beam_size(), 1)
        with patch.dict(os.environ, {"WHISPER_BEAM_SIZE": "3"}):
            self.assertEqual(stt._whisper_beam_size(), 3)


if __name__ == "__main__":
    unittest.main()
