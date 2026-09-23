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


if __name__ == "__main__":
    unittest.main()
