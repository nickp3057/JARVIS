"""Tests for standalone/headless streamed response orchestration."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from speech import voice_loop


class _Runtime:
    brain_online = True

    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.consumed = False

    def respond_streaming(self, _text: str):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error
        self.consumed = True


class VoiceLoopStreamingTests(unittest.TestCase):
    def test_streaming_flag_is_read_live(self) -> None:
        with patch.dict(os.environ, {"STREAMING_TTS": "YES"}, clear=False):
            self.assertTrue(voice_loop._streaming_enabled())
        with patch.dict(os.environ, {"STREAMING_TTS": "off"}, clear=False):
            self.assertFalse(voice_loop._streaming_enabled())

    def test_stream_response_speaks_chunks_and_returns_full_reply(self) -> None:
        runtime = _Runtime(["First sentence. ", "Second sentence."])
        spoken: list[str] = []

        def consume(_stream, chunks, **_kwargs) -> bool:
            spoken.extend(chunks)
            return False

        with patch.object(voice_loop, "_speak_stream_interruptible", side_effect=consume):
            reply, interrupted = voice_loop._stream_response_interruptible(
                runtime, Mock(), "hello",
            )
        self.assertEqual(reply, "First sentence. Second sentence.")
        self.assertEqual(spoken, ["First sentence. ", "Second sentence."])
        self.assertFalse(interrupted)
        self.assertTrue(runtime.consumed)

    def test_interrupted_audio_still_finishes_model_stream_for_history(self) -> None:
        runtime = _Runtime(["One. ", "Two. ", "Three."])

        def interrupt_after_first(_stream, chunks, **_kwargs) -> bool:
            next(chunks)
            return True

        with patch.object(
            voice_loop, "_speak_stream_interruptible", side_effect=interrupt_after_first,
        ):
            reply, interrupted = voice_loop._stream_response_interruptible(
                runtime, Mock(), "hello",
            )
        self.assertTrue(interrupted)
        self.assertTrue(runtime.consumed)
        self.assertEqual(reply, "One. Two. Three.")

    def test_streaming_failure_surfaces_fallback_without_deadlock(self) -> None:
        runtime = _Runtime(["Partial. "], error=RuntimeError("offline"))
        spoken: list[str] = []

        def consume(_stream, chunks, **_kwargs) -> bool:
            spoken.extend(chunks)
            return False

        with patch.object(voice_loop, "_speak_stream_interruptible", side_effect=consume), patch.object(
            voice_loop.log, "exception",
        ):
            reply, interrupted = voice_loop._stream_response_interruptible(
                runtime, Mock(), "hello",
            )
        self.assertFalse(interrupted)
        self.assertIn("Partial", reply)
        self.assertIn("Something went wrong", reply)
        self.assertEqual("".join(spoken), "Partial. Something went wrong, sir.")

    def test_empty_blocking_reply_does_not_start_watcher(self) -> None:
        stream = Mock()
        self.assertFalse(voice_loop._speak_interruptible(stream, "   "))
        stream.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
