"""Microphone capture: the stream's queue and rolling buffer, MIC_DEVICE,
and recording one utterance until silence. A fake sounddevice stands in."""
import os
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from speech import audio

LOUD = np.full(audio.FRAME_SAMPLES, int(audio.RMS_THRESHOLD * 4), dtype=np.int16)
QUIET = np.zeros(audio.FRAME_SAMPLES, dtype=np.int16)


class FakeInputStream:
    last = None
    refuse_rates: set = set()

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        if kwargs["samplerate"] in FakeInputStream.refuse_rates:
            raise RuntimeError("Invalid sample rate [PaErrorCode -9997]")
        self.kwargs, self.started, self.closed = kwargs, False, False
        FakeInputStream.last = self

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def feed(self, frame: np.ndarray, status=None) -> None:  # noqa: ANN001
        block = frame.reshape(len(frame), -1) if frame.ndim > 1 else frame.reshape(-1, 1)
        self.kwargs["callback"](block, len(block), None, status)


class MicStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        def query_devices(device=None, kind=None):  # noqa: ANN001, ANN202
            return {"name": "CMTECK: USB Audio (hw:0,0)", "default_samplerate": 48000.0, "max_input_channels": 2}
        fake = types.SimpleNamespace(InputStream=FakeInputStream, query_devices=query_devices)
        patch.dict(sys.modules, {"sounddevice": fake}).start()
        FakeInputStream.refuse_rates = set()
        self.addCleanup(patch.stopall)

    def test_frames_queue_and_rolling_buffer(self) -> None:
        with patch.dict(os.environ, {"MIC_DEVICE": ""}), audio.MicStream(rolling_buffer_frames=3) as mic:
            fake = FakeInputStream.last
            self.assertTrue(fake.started)
            self.assertIsNone(fake.kwargs["device"])
            for value in (1, 2, 3, 4):
                fake.feed(np.full(audio.FRAME_SAMPLES, value, dtype=np.int16), status="overflow")
            self.assertEqual(int(mic.read(timeout=0)[0]), 1)
            mic.drain()
            self.assertIsNone(mic.read(timeout=0))
            recent = mic.get_recent_audio(seconds=2 * audio.FRAME_MS / 1000)
            self.assertEqual(recent.size, 2 * audio.FRAME_SAMPLES)
            self.assertEqual((int(recent[0]), int(recent[-1])), (3, 4))  # only the newest frames kept
            self.assertEqual(mic.get_recent_audio(0).size, 0)
        self.assertTrue(fake.closed)

    def test_mic_device_by_index_or_name(self) -> None:
        for value, expected in (("2", 2), ("CMTECK", "CMTECK")):
            with patch.dict(os.environ, {"MIC_DEVICE": value}), audio.MicStream():
                self.assertEqual(FakeInputStream.last.kwargs["device"], expected)


    def test_device_that_refuses_16k_records_native_and_converts(self) -> None:
        FakeInputStream.refuse_rates = {audio.SAMPLE_RATE}
        with patch.dict(os.environ, {"MIC_DEVICE": "CMTECK"}), audio.MicStream() as mic:
            fake = FakeInputStream.last
            self.assertEqual((fake.kwargs["samplerate"], fake.kwargs["channels"]), (48000, 1))
            self.assertEqual(fake.kwargs["blocksize"], 3 * audio.FRAME_SAMPLES)
            fake.feed(np.repeat(np.arange(audio.FRAME_SAMPLES, dtype=np.int16), 3))
            frame = mic.read(timeout=0)
            self.assertEqual(frame.size, audio.FRAME_SAMPLES)
            self.assertEqual((int(frame[0]), int(frame[-1])), (0, audio.FRAME_SAMPLES - 1))

    def test_stereo_and_odd_rates_convert_to_mono_16k(self) -> None:
        stereo = np.stack([np.full(441, 100, np.int16), np.full(441, -5, np.int16)], axis=1)
        out = audio.to_mono_16k(stereo, 44100)
        self.assertEqual(out.size, 160)
        self.assertTrue((out == 100).all())  # first channel only
        self.assertTrue((audio.to_mono_16k(np.arange(4, dtype=np.int16), 16000) == np.arange(4)).all())

    def test_every_open_failing_raises(self) -> None:
        FakeInputStream.refuse_rates = {audio.SAMPLE_RATE, 48000}
        with self.assertRaises(RuntimeError), audio.MicStream():
            pass


class FakeStream:
    def __init__(self, frames) -> None:  # noqa: ANN001
        self.frames = list(frames)

    def read(self, timeout=None):  # noqa: ANN001
        return self.frames.pop(0) if self.frames else QUIET


class RecordTests(unittest.TestCase):
    def test_rms(self) -> None:
        self.assertEqual(audio.rms(np.zeros(0, dtype=np.int16)), 0.0)
        self.assertAlmostEqual(audio.rms(np.full(10, 3, dtype=np.int16)), 3.0)

    def test_records_until_silence(self) -> None:
        frames = [QUIET, QUIET, LOUD, LOUD] + [QUIET] * (audio.SILENCE_END_FRAMES + 5)
        out = audio.record_until_silence(FakeStream(frames), max_seconds=5, start_timeout=2)
        self.assertEqual(out.size, (2 + audio.SILENCE_END_FRAMES) * audio.FRAME_SAMPLES)

    def test_nobody_talks_and_the_cap(self) -> None:
        self.assertIsNone(audio.record_until_silence(FakeStream([]), max_seconds=5, start_timeout=0.3))
        endless = FakeStream([LOUD] * 1000)
        out = audio.record_until_silence(endless, max_seconds=0.8, start_timeout=1)
        self.assertEqual(out.size, int(0.8 * 1000 / audio.FRAME_MS) * audio.FRAME_SAMPLES)


if __name__ == "__main__":
    unittest.main()
