"""The bedtime routine: one question about the alarm, then lights out."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from brain import voice_presence, wake_alarm
from speech import goodnight, room_voice

NIGHT = datetime(2026, 10, 28, 22, 30)  # a Wednesday night; Thursday's alarm is 7:00


class Script:
    """Plays Nick's side: each listen() returns the next answer."""

    def __init__(self, *answers) -> None:  # noqa: ANN002
        self.answers, self.said, self.lights = list(answers), [], 0

    def say(self, text: str) -> None:
        self.said.append(text)

    def listen(self):  # noqa: ANN201
        return self.answers.pop(0) if self.answers else None

    def lights_out(self) -> None:
        self.lights += 1


class GoodnightTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(wake_alarm, "_FILE", Path(tmp.name) / "wake.json").start()
        patch.dict(os.environ, {"ADDRESS": "sir"}).start()
        self.addCleanup(patch.stopall)
        wake_alarm.set_schedule(["08:00", "07:00", "08:00", "07:00", "08:00", "09:00", "07:00"])

    def night(self, *answers) -> tuple[Script, str]:  # noqa: ANN002
        script = Script(*answers)
        return script, goodnight.run(script.say, script.listen, script.lights_out, now=NIGHT)

    def tomorrow(self) -> datetime | None:
        return wake_alarm.next_time(wake_alarm.load(), NIGHT)

    def test_yes_keeps_it_and_turns_the_lights_off(self) -> None:
        script, outcome = self.night("Yeah.")
        self.assertEqual(script.said, ["Goodnight, sir. Your alarm's set for 7:00. Is that fine?",
                                       "Sleep well, sir."])
        self.assertEqual((outcome, script.lights), ("kept", 1))
        self.assertEqual(self.tomorrow().strftime("%H:%M"), "07:00")

    def test_silence_counts_as_yes(self) -> None:
        script, outcome = self.night()
        self.assertEqual((outcome, script.lights, script.said[-1]), ("kept", 1, "Sleep well, sir."))

    def test_a_new_time_is_for_tomorrow_only(self) -> None:
        script, outcome = self.night("No, make it 6:30.")
        self.assertEqual(script.said[-1], "6:30, then. Sleep well, sir.")
        self.assertEqual(self.tomorrow(), datetime(2026, 10, 29, 6, 30))
        week_later = wake_alarm.next_time(wake_alarm.load(), datetime(2026, 11, 4, 22, 0))
        self.assertEqual(week_later.strftime("%H:%M"), "07:00")  # next Thursday is back to normal

    def test_skip_the_alarm(self) -> None:
        script, outcome = self.night("No alarm tomorrow, I'm sleeping in.")
        self.assertEqual((outcome, script.said[-1]), ("skipped", "No alarm, then. Sleep well, sir."))
        self.assertNotEqual(self.tomorrow().date(), datetime(2026, 10, 29).date())

    def test_bare_no_asks_what_time(self) -> None:
        script, outcome = self.night("No.", "Seven thirty.")
        self.assertEqual(script.said[1], "What time, sir?")
        self.assertEqual(self.tomorrow(), datetime(2026, 10, 29, 7, 30))
        script, outcome = self.night("Nope.", None)  # no answer to "what time": keep it
        self.assertEqual(outcome, "kept")

    def test_no_alarm_set(self) -> None:
        wake_alarm.cancel()
        script, outcome = self.night("Yes, eight please.")
        self.assertEqual(script.said[0], "Goodnight, sir. There's no alarm for the morning. Want one?")
        self.assertEqual(self.tomorrow(), datetime(2026, 10, 29, 8, 0))
        wake_alarm.cancel()
        script, outcome = self.night("Yes.", "Half past six.")
        self.assertEqual(script.said[1], "What time, sir?")
        self.assertEqual(self.tomorrow(), datetime(2026, 10, 29, 6, 30))
        wake_alarm.cancel()
        script, outcome = self.night("No thanks.")
        self.assertEqual((outcome, script.said[-1], script.lights), ("none", "Sleep well, sir.", 1))

    def test_after_midnight_tomorrow_means_today(self) -> None:
        script = Script("make it 9")
        goodnight.run(script.say, script.listen, script.lights_out, now=datetime(2026, 10, 29, 1, 15))
        self.assertEqual(wake_alarm.next_time(wake_alarm.load(), datetime(2026, 10, 29, 1, 15)),
                         datetime(2026, 10, 29, 9, 0))


class UnderstandingTests(unittest.TestCase):
    def test_triggers(self) -> None:
        for said in ("Goodnight.", "Good night, Jarvis!", "Jarvis goodnight", "I'm going to bed.", "Bedtime"):
            self.assertTrue(goodnight.is_goodnight(said), said)
        for said in ("goodnight scene", "turn off the lights", "good morning"):
            self.assertFalse(goodnight.is_goodnight(said), said)

    def test_times(self) -> None:
        cases = {"6:30": "06:30", "6.30": "06:30", "make it 8": "08:00", "seven thirty": "07:30",
                 "six o'clock": "06:00", "half past six": "06:30", "7 pm": "19:00", "12": "12:00",
                 "12 am": "00:00", "six forty-five": "06:45", "6 45": "06:45", "set it for 10:15 a.m.": "10:15",
                 "yes": None, "25:00": None}
        for said, hhmm in cases.items():
            self.assertEqual(goodnight.parse_spoken_time(said), hhmm, said)

    def test_answers(self) -> None:
        cases = {"Yes.": "yes", "Yeah, that's fine": "yes", "Sounds good.": "yes", "": "silent",
                 None: "silent", "No.": "no", "Nope": "no", "skip it": "skip", "turn it off": "skip",
                 "make it 6": "change", "what's the weather": "unclear"}
        for said, kind in cases.items():
            self.assertEqual(goodnight.understand(said)[0], kind, said)


class RoomVoiceRoutineTests(unittest.TestCase):
    def test_goodnight_runs_in_the_room_voice_without_claude(self) -> None:
        voice_presence.reset_for_tests()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(__import__("brain.voice_shortcuts", fromlist=["x"]), "_FILE", Path(tmp.name) / "s.json")
        p.start()
        self.addCleanup(p.stop)
        runtime = Mock()
        final = Mock()
        final.transcribe.side_effect = ["Hey Jarvis, goodnight.", "Yes."]
        voice = room_voice.RoomVoice(runtime, detector=Mock(), final=final, live=Mock())
        voice.stream = Mock()
        with patch.object(voice, "_capture", return_value=np.ones(1600, dtype=np.int16)), \
                patch("speech.tts.speak") as speak, \
                patch.object(voice, "_lights_out") as lights, \
                patch.object(goodnight, "_morning", return_value=("2026-10-29", datetime(2026, 10, 29, 7))):
            follow_up = voice._handle(np.ones(1600, dtype=np.int16))
        self.assertFalse(follow_up)
        runtime.respond.assert_not_called()
        voice.stream.drain.assert_called()  # its own question isn't heard as the answer
        lights.assert_called_once()
        self.assertEqual([c.args[0] for c in speak.call_args_list],
                         ["Goodnight, sir. Your alarm's set for 7:00. Is that fine?", "Sleep well, sir."])
        self.assertTrue(all(c.kwargs["cache"] for c in speak.call_args_list))
        self.assertEqual(voice_presence.current()["phase"], "idle")

    def test_lights_out_uses_the_goodnight_scene(self) -> None:
        voice = room_voice.RoomVoice(Mock(), detector=Mock(), final=Mock(), live=Mock())

        class Inline:
            def __init__(self, target, name, daemon):  # noqa: ANN001
                self.target = target

            def start(self) -> None:
                self.target()

        scenes = {"scenes": [{"entity_id": "scene.goodnight", "name": "Goodnight"}]}
        with patch.object(room_voice.threading, "Thread", Inline), \
                patch("app.status_hub.home_scenes", return_value=scenes), \
                patch("app.status_hub.activate_scene") as activate:
            voice._lights_out()
        activate.assert_called_once_with("scene.goodnight")
        with patch.object(room_voice.threading, "Thread", Inline), \
                patch("app.status_hub.home_scenes", return_value={"scenes": []}), \
                patch("app.status_hub.activate_scene") as activate, \
                self.assertLogs("jarvis.speech.room_voice", level="WARNING"):
            voice._lights_out()
        activate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
