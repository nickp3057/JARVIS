"""The film setting JARVIS off: a wake while the TV plays that sounds
nothing like Nick and never says "Jarvis" is dropped (speech/room_voice.py)."""
import os
import unittest
from unittest.mock import patch

from brain import voice_trust
from speech import room_voice


def guest(score):
    return voice_trust.guest("voiceprint mismatch", score=score)


class TvWakeFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        p = patch.dict(os.environ, {"VOICE_TV_FLOOR": "0.30"})
        p.start()
        self.addCleanup(p.stop)

    def test_film_dialogue_is_dropped_while_the_tv_plays(self) -> None:
        with patch.object(room_voice, "_tv_playing", return_value=True):
            self.assertTrue(room_voice._sounds_like_the_tv("This is not holding this", guest(0.21)))

    def test_saying_jarvis_or_sounding_like_nick_still_counts(self) -> None:
        with patch.object(room_voice, "_tv_playing", return_value=True):
            self.assertFalse(room_voice._sounds_like_the_tv("Hey, Jarvis, pause", guest(0.12)))
            self.assertFalse(room_voice._sounds_like_the_tv("movie time", guest(0.44)))
            self.assertFalse(room_voice._sounds_like_the_tv("lights on", voice_trust.Speaker("nick", True)))

    def test_only_while_the_tv_plays_and_can_be_switched_off(self) -> None:
        with patch.object(room_voice, "_tv_playing", return_value=False):
            self.assertFalse(room_voice._sounds_like_the_tv("lights on", guest(0.1)))
        with patch.object(room_voice, "_tv_playing", return_value=True), \
                patch.dict(os.environ, {"VOICE_TV_FLOOR": "0"}):
            self.assertFalse(room_voice._sounds_like_the_tv("lights on", guest(0.1)))


class DeskMicTests(unittest.TestCase):
    def test_desk_mic_switch(self) -> None:
        for value, on in (("0", False), ("off", False), ("1", True), ("", True)):
            with patch.dict(os.environ, {"DESK_MIC": value}):
                self.assertEqual(room_voice.desk_mic_on(), on, value)


if __name__ == "__main__":
    unittest.main()
