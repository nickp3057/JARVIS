"""Tests for sentence buffering and streaming TTS control flow."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from speech import tts


class StreamingTTSTests(unittest.TestCase):
    def test_sentence_splitter_keeps_tail_and_decimal_together(self) -> None:
        ready, tail = tts._split_ready_sentences("Temperature is 72.5 degrees. Next sentence")
        self.assertEqual(ready, ["Temperature is 72.5 degrees."])
        self.assertEqual(tail, "Next sentence")

    def test_sentence_splitter_supports_quotes_and_multiple_marks(self) -> None:
        ready, tail = tts._split_ready_sentences('He said "Done!"  Really? Yes')
        self.assertEqual(ready, ['He said "Done!"', "Really?"])
        self.assertEqual(tail, "Yes")

    def test_speak_stream_speaks_complete_sentences_then_tail_in_order(self) -> None:
        with patch.object(tts, "speak") as speak:
            spoke = tts.speak_stream(iter(("First sentence. Sec", "ond sentence! Tail")))
        self.assertTrue(spoke)
        self.assertEqual(
            [call.args[0] for call in speak.call_args_list],
            ["First sentence.", "Second sentence!", "Tail"],
        )

    def test_speak_stream_skips_empty_chunks(self) -> None:
        with patch.object(tts, "speak") as speak:
            spoke = tts.speak_stream(iter(("", None, "   ")))
        self.assertFalse(spoke)
        speak.assert_not_called()

    def test_speak_stream_honors_preexisting_interrupt(self) -> None:
        interrupt = threading.Event()
        interrupt.set()
        with patch.object(tts, "speak") as speak:
            spoke = tts.speak_stream(iter(("Never spoken.",)), interrupt_event=interrupt)
        self.assertFalse(spoke)
        speak.assert_not_called()

    def test_speak_stream_stops_after_interrupt_during_first_sentence(self) -> None:
        interrupt = threading.Event()

        def interrupt_on_speak(*_args, **_kwargs) -> None:
            interrupt.set()

        with patch.object(tts, "speak", side_effect=interrupt_on_speak) as speak:
            spoke = tts.speak_stream(
                iter(("First. Second. ", "Third.")),
                interrupt_event=interrupt,
            )
        self.assertTrue(spoke)
        self.assertEqual(speak.call_count, 1)


if __name__ == "__main__":
    unittest.main()
