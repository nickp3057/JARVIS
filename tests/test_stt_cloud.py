"""Cloud STT: picked only with a key, live captions stay local, failures fall back."""
import os
import unittest
from unittest import mock

import numpy as np

from speech import stt


class CloudSttTests(unittest.TestCase):
    def test_provider_needs_its_key(self):
        with mock.patch.dict(os.environ, {"STT_PROVIDER": "elevenlabs", "ELEVENLABS_API_KEY": ""}):
            self.assertEqual(stt.cloud_provider(), "")
        with mock.patch.dict(os.environ, {"STT_PROVIDER": "elevenlabs", "ELEVENLABS_API_KEY": "k"}):
            self.assertEqual(stt.cloud_provider(), "elevenlabs")

    def test_main_transcriber_uses_cloud_and_falls_back(self):
        audio = (np.sin(np.linspace(0, 400, 16000)) * 8000).astype(np.int16)
        env = {"STT_PROVIDER": "elevenlabs", "ELEVENLABS_API_KEY": "k"}
        with mock.patch.dict(os.environ, env), mock.patch.object(stt, "cloud_transcribe", return_value="lights on"):
            self.assertEqual(stt.Transcriber().transcribe(audio), "lights on")
        local = stt.Transcriber()
        local._model = mock.Mock()
        local._model.transcribe.return_value = ([mock.Mock(text=" local ")], None)
        with mock.patch.dict(os.environ, env), mock.patch.object(stt, "cloud_transcribe", return_value=None):
            self.assertEqual(local.transcribe(audio), "local")

    def test_live_model_never_goes_to_cloud(self):
        live = stt.Transcriber(model_size="tiny.en")
        live._model = mock.Mock()
        live._model.transcribe.return_value = ([mock.Mock(text="hi")], None)
        env = {"STT_PROVIDER": "elevenlabs", "ELEVENLABS_API_KEY": "k"}
        with mock.patch.dict(os.environ, env), mock.patch.object(stt, "cloud_transcribe") as cloud:
            live.transcribe(np.ones(1600, dtype=np.int16) * 9000)
        cloud.assert_not_called()


if __name__ == "__main__":
    unittest.main()
