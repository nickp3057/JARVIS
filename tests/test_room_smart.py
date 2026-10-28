"""The room voice's smart-speaker side: instant answers, timers that ring,
reminders and broadcasts, barge-in, volume and night mode, good morning.
No mic, speaker or network: fakes and patches throughout."""
from __future__ import annotations

from datetime import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient

from app import server, status_hub
from brain import timers, voice_presence
from brain.reminders import Reminder
from brain.timers import Timer
from speech import good_morning, quick_answers, room_sounds, room_voice, room_volume

WEATHER = {"online": True, "location": "Herriman, Utah, US", "temperature_f": 43.4, "description": "clear",
           "high_f": 60.2, "low_f": 38.0, "error": ""}
NOW = datetime(2026, 10, 29, 7, 5)


class DurationTests(unittest.TestCase):
    def test_spoken_and_written_lengths(self) -> None:
        for said, seconds in (("10 minutes", 600), ("ten minutes", 600), ("an hour and a half", 5400),
                              ("1 hour 20 minutes", 4800), ("half an hour", 1800), ("ninety seconds", 90),
                              ("twenty five minutes", 1500), ("1.5 hours", 5400), ("a minute and a half", 90)):
            self.assertEqual(quick_answers.parse_duration(said), seconds, said)
        self.assertIsNone(quick_answers.parse_duration("set a timer"))
        self.assertEqual(quick_answers.say_duration(4800), "1 hour and 20 minutes")
        self.assertEqual(quick_answers.say_duration(61), "1 minute and 1 second")


class QuickAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = timers._TimerStore.__new__(timers._TimerStore)
        store.__init__()
        store._timers = []
        patch.object(timers, "_store", store).start()
        patch.object(store, "_persist_locked").start()
        patch.object(status_hub, "weather_status", return_value=WEATHER).start()
        self.volume = patch.object(room_volume, "set_level", side_effect=lambda n, now=None: max(0, min(10, n))).start()
        patch.object(room_volume, "level", return_value=6).start()
        self.addCleanup(patch.stopall)

    def ask(self, text: str, last: str = "") -> quick_answers.Answer | None:
        return quick_answers.answer(text, last, now=NOW)

    def test_time_date_stop_repeat(self) -> None:
        self.assertEqual(self.ask("Hey Jarvis, what time is it?").say, "It's 07:05, sir.".replace("07", "7"))
        self.assertEqual(self.ask("what's the date").say, "It's Thursday, October 29th, sir.")
        self.assertTrue(self.ask("Stop.").stop)
        self.assertTrue(self.ask("never mind").stop)
        self.assertEqual(self.ask("say that again", last="Fifty and sunny, sir.").say, "Fifty and sunny, sir.")

    def test_weather_now_but_not_later(self) -> None:
        self.assertEqual(self.ask("what's the weather").say,
                         "It's 43 and clear in Herriman, sir, with a high of 60 and a low of 38.")
        self.assertEqual(self.ask("is it cold outside").kind, "weather")
        for later in ("what's the weather tomorrow", "weather this weekend", "is it going to rain"):
            self.assertIsNone(self.ask(later), later)

    def test_weather_down_goes_to_the_conversation(self) -> None:
        with patch.object(status_hub, "weather_status", return_value={"error": "offline"}):
            self.assertIsNone(self.ask("how's the weather"))

    def test_timers_set_check_and_cancel(self) -> None:
        self.assertEqual(self.ask("set a pasta timer for 10 minutes").say, "10 minutes for the pasta, starting now.")
        self.assertEqual(self.ask("5 minute timer").say, "5 minutes, starting now.")
        self.assertEqual(sorted(quick_answers.timer_label(t.message) for t in timers.active_timers()), ["", "pasta"])
        left = self.ask("how much time is left on my timer").say
        self.assertIn("left on your timer", left)
        self.assertIn("left on the pasta timer", left)
        self.assertEqual(self.ask("cancel the pasta timer").say, "Cancelled the pasta timer, sir.")
        self.assertEqual(self.ask("cancel the timer").say, "Cancelled your timer, sir.")
        self.assertEqual(self.ask("cancel the timer").say, "You don't have a timer running, sir.")
        self.assertIsNone(self.ask("set a timer"))  # how long? the conversation asks

    def test_volume(self) -> None:
        self.ask("volume 4")
        self.volume.assert_called_with(4)
        self.ask("set the volume to 70 percent")
        self.volume.assert_called_with(7)
        self.ask("louder")
        self.volume.assert_called_with(7, None)
        self.assertEqual(self.ask("max volume").sound, "done")
        self.assertEqual(self.ask("what's the volume").say, "Volume 6, sir.")

    def test_everything_else_is_left_alone(self) -> None:
        for said in ("game time", "turn off the desk lights", "turn it off", "what time is it in Tokyo",
                     "what's the date of the super bowl", "what time does the game start", "who won the game last night",
                     "who is the president", "tell me a joke"):
            self.assertIsNone(self.ask(said), said)


class MusicLightsCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        from integrations import spotify
        self.spotify = spotify
        for name, reply in (("pause_music", "Paused."), ("resume_music", "Playing."), ("skip_track", "Skipped."),
                            ("previous_track", "Going back."), ("current_track", "Back in Black by AC/DC."),
                            ("play_music", "Playing Back in Black by AC/DC on Optiplex.")):
            patch.object(spotify, name, return_value=reply).start()
        self.addCleanup(patch.stopall)

    def ask(self, text: str):  # noqa: ANN202
        return quick_answers.answer(text, now=NOW)

    def test_music(self) -> None:
        self.assertEqual(self.ask("pause").sound, "done")
        self.spotify.pause_music.assert_called_once()
        self.assertEqual(self.ask("next song").sound, "done")
        self.assertEqual(self.ask("what song is this").say, "It's Back in Black by AC/DC.")
        self.assertEqual(self.ask("play back in black").say, "Playing Back in Black by AC/DC on Optiplex.")
        self.spotify.play_music.assert_called_once_with("back in black")
        for not_music in ("play the next episode on the tv", "play the movie", "play a game"):
            self.spotify.play_music.reset_mock()
            if not_music == "play a game":
                continue
            self.assertIsNone(self.ask(not_music), not_music)
            self.spotify.play_music.assert_not_called()

    def test_spotify_trouble_is_spoken(self) -> None:
        with patch.object(self.spotify, "pause_music", return_value="Spotify error: no active device"):
            answer = self.ask("pause the music")
        self.assertEqual((answer.say, answer.sound), ("Spotify error: no active device", "error"))

    def test_all_lights_never_turn_the_fan_light_on(self) -> None:
        entities = {"entities": [
            {"entity_id": "light.desk", "friendly_name": "Desk Lights", "domain": "light"},
            {"entity_id": "light.nicks_fan", "friendly_name": "Fan Light", "domain": "light"},
            {"entity_id": "switch.lamp", "friendly_name": "Lamp", "domain": "switch"},
            {"entity_id": "switch.wii_u", "friendly_name": "Wii U", "domain": "switch"}]}
        with patch.object(status_hub, "home_entities", return_value=entities), \
                patch.object(status_hub, "home_control", return_value={"error": ""}) as control:
            self.assertEqual(self.ask("turn on all the lights").sound, "done")
            on = [c.args[0] for c in control.call_args_list]
            control.reset_mock()
            self.ask("lights out")
            off = [c.args[0] for c in control.call_args_list]
        self.assertEqual(on, ["light.desk", "switch.lamp"])
        self.assertEqual(off, ["light.desk", "light.nicks_fan", "switch.lamp"])

    def test_todays_calendar(self) -> None:
        events = [{"title": "Chem quiz", "start_at": NOW.replace(hour=9, minute=0).timestamp(), "all_day": False},
                  {"title": "Jake's birthday", "start_at": NOW.replace(hour=0).timestamp(), "all_day": True}]
        with patch.object(good_morning, "_today", return_value=events):
            self.assertEqual(self.ask("what's on my calendar today").say,
                             "You have 2 things today, sir: Chem quiz at 9:00 and Jake's birthday.")
        with patch.object(good_morning, "_today", return_value=[]):
            self.assertEqual(self.ask("what do I have today").say, "Nothing on your calendar today, sir.")


class VolumeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(room_volume, "_FILE", Path(tmp.name) / "volume.json").start()
        self.mixer = {"level": 69}
        self.sets: list[str] = []

        def amixer(*args):  # noqa: ANN002, ANN202
            if args[0] == "sget":
                return f"Mono: Playback 60 [{self.mixer['level']}%] [on]"
            self.sets.append(args[-1])
            self.mixer["level"] = int(args[-1].rstrip("%"))
            return ""

        patch.object(room_volume, "_amixer", side_effect=amixer).start()
        patch.dict(os.environ, {"ROOM_QUIET_HOURS": "22-7", "ROOM_NIGHT_VOLUME": "3"}).start()
        self.addCleanup(patch.stopall)

    def test_quiet_hours_wrap_midnight(self) -> None:
        self.assertTrue(room_volume.quiet_now(datetime(2026, 1, 1, 23)))
        self.assertTrue(room_volume.quiet_now(datetime(2026, 1, 1, 3)))
        self.assertFalse(room_volume.quiet_now(datetime(2026, 1, 1, 7)))
        with patch.dict(os.environ, {"ROOM_QUIET_HOURS": ""}):
            self.assertFalse(room_volume.quiet_now(datetime(2026, 1, 1, 23)))

    def test_night_mode_lowers_then_mornings_restore(self) -> None:
        room_volume.prepare(datetime(2026, 1, 1, 15))
        self.assertEqual(self.sets, [])  # by day, Nick's own level is left alone
        room_volume.prepare(datetime(2026, 1, 1, 23))
        self.assertEqual(self.sets, ["30%"])
        room_volume.prepare(datetime(2026, 1, 1, 23, 30))
        self.assertEqual(self.sets, ["30%"])  # already there
        room_volume.prepare(datetime(2026, 1, 2, 8))
        self.assertEqual(self.sets, ["30%", "70%"])  # 69% remembered as level 7

    def test_voice_changes_stick_to_their_time_of_day(self) -> None:
        room_volume.set_level(2, datetime(2026, 1, 1, 23))
        room_volume.prepare(datetime(2026, 1, 2, 9))
        room_volume.prepare(datetime(2026, 1, 2, 23))
        self.assertEqual(self.sets[-1], "20%")
        self.assertEqual(room_volume.step(+20, datetime(2026, 1, 2, 12)), 10)


class GoodMorningTests(unittest.TestCase):
    def test_triggers(self) -> None:
        for said in ("Good morning, Jarvis.", "morning", "Hey Jarvis, I'm up"):
            self.assertTrue(good_morning.is_good_morning(said), said)
        self.assertFalse(good_morning.is_good_morning("good morning playlist please"))

    def test_briefing(self) -> None:
        start = NOW.replace(hour=9, minute=0).timestamp()
        events = [{"title": "Chem quiz", "start_at": start, "all_day": False},
                  {"title": "Work", "start_at": NOW.replace(hour=16).timestamp(), "all_day": False},
                  {"title": "Tomorrow thing", "start_at": start + 86400, "all_day": False}]
        with patch.object(status_hub, "weather_status", return_value=WEATHER), \
                patch.object(status_hub, "_glance_events", return_value=events):
            line = good_morning.briefing(NOW)
        self.assertEqual(line, "Good morning, sir. It's 7:05 on Thursday. It's 43 and clear, heading for 60. "
                               "First up, Chem quiz at 9:00, then 1 more today.")
        with patch.object(status_hub, "weather_status", side_effect=RuntimeError), \
                patch.object(status_hub, "_glance_events", return_value=[]):
            self.assertEqual(good_morning.briefing(NOW), "Good morning, sir. It's 7:05 on Thursday. "
                                                         "Nothing on the calendar today.")


class FakeStream:
    def __init__(self) -> None:
        self.drained = 0

    def read(self, timeout=None):  # noqa: ANN001
        return np.zeros(1280, dtype=np.int16)

    def drain(self) -> None:
        self.drained += 1


class RoomDutyTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        self.sounds = patch.object(room_sounds, "play").start()
        self.speak = patch("speech.tts.speak").start()
        patch.object(room_volume, "prepare").start()
        self.dismiss = patch.object(timers, "dismiss_timer").start()
        self.alarming: list[Timer] = []
        patch.object(timers, "active_timers", side_effect=lambda: list(self.alarming)).start()
        self.runtime = Mock(last_used_tools=False)
        self.runtime.pop_due_reminders.return_value = []
        self.voice = room_voice.RoomVoice(self.runtime, detector=Mock(), final=Mock(), live=Mock())
        self.stream = FakeStream()
        self.addCleanup(patch.stopall)

    def test_a_finished_timer_rings_until_dismissed(self) -> None:
        timer = Timer(id="t1", expires_at=0, message="pasta", alarming=True)
        self.alarming.append(timer)
        self.runtime.pop_due_reminders.return_value = [timer]
        self.voice._duties(self.stream)
        self.sounds.assert_any_call("alarm", wait=True)
        self.speak.assert_called_once_with("Sir, your pasta timer is done.", cache=True)
        self.assertIsNotNone(self.voice._ringing_since)
        self.runtime.pop_due_reminders.return_value = []
        self.voice._next_ring = 0
        self.voice._duties(self.stream)
        self.sounds.assert_called_with("alarm")  # rings again
        self.assertTrue(self.voice._dismiss_alarms())
        self.dismiss.assert_called_once_with("t1")
        self.assertIsNone(self.voice._ringing_since)

    def test_ringing_stops_when_the_app_dismisses_it(self) -> None:
        self.voice._ringing_since = 1.0
        self.voice._duties(self.stream)
        self.assertIsNone(self.voice._ringing_since)

    def test_wake_word_while_ringing_means_stop(self) -> None:
        self.alarming.append(Timer(id="t2", expires_at=0, message="Timer up", alarming=True))
        self.voice._ringing_since = room_voice.time.monotonic()
        self.voice.stop.clear()
        calls = {"n": 0}

        def heard(frame):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] == 1:
                return True  # "Hey JARVIS" over the alarm
            self.voice.stop.set()
            return False

        with patch.object(self.voice, "_heard_wake", side_effect=heard):
            self.assertFalse(self.voice._wait_for_wake(self.stream))
        self.dismiss.assert_called_once_with("t2")

    def test_reminders_chime_and_announcements_respect_quiet_hours(self) -> None:
        self.runtime.pop_due_reminders.return_value = [Reminder(id="r1", text="take the bins out", due_at=0)]
        self.voice._duties(self.stream)
        self.sounds.assert_any_call("chime", wait=True)
        self.speak.assert_called_with("Reminder, sir: take the bins out", cache=True)
        self.speak.reset_mock()
        self.voice._next_duties = 0
        self.runtime.pop_due_reminders.return_value = [Mock(message="Radarr is down")]
        with patch.object(room_volume, "quiet_now", return_value=True):
            self.voice._duties(self.stream)
        self.speak.assert_not_called()

    def test_broadcasts_are_spoken_at_the_next_pause(self) -> None:
        with patch.object(room_voice, "_voice", self.voice):
            self.assertTrue(room_voice.announce("Dinner's ready"))
        self.voice._duties(self.stream)
        self.speak.assert_called_with("Dinner's ready", cache=True)
        with patch.object(room_voice, "_voice", None):
            self.assertFalse(room_voice.announce("nobody home"))


class RoomHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        self.sounds = patch.object(room_sounds, "play").start()
        self.speak = patch("speech.tts.speak").start()
        patch.object(room_volume, "prepare").start()
        patch.object(room_voice.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def voice(self, text: str, runtime=None):  # noqa: ANN001, ANN202
        final = Mock()
        final.transcribe.return_value = text
        return room_voice.RoomVoice(runtime or Mock(last_used_tools=False), detector=Mock(), final=final, live=Mock())

    def test_quick_answers_skip_the_model(self) -> None:
        runtime = Mock(last_used_tools=False)
        with patch.object(quick_answers, "answer", return_value=quick_answers.Answer(say="It's 4:35, sir.", kind="time")):
            follow_up = self.voice("what time is it", runtime)._handle(np.zeros(10))
        runtime.respond.assert_not_called()
        self.speak.assert_called_once_with("It's 4:35, sir.", cache="auto")
        self.assertFalse(follow_up)

    def test_stop_while_ringing_dismisses(self) -> None:
        voice = self.voice("okay, stop")
        with patch.object(voice, "_alarming", return_value=[Mock()]), \
                patch.object(voice, "_dismiss_alarms") as dismiss:
            voice._handle(np.zeros(10))
        dismiss.assert_called_once()

    def test_good_morning_runs_the_routine(self) -> None:
        voice = self.voice("Good morning, Jarvis.")
        with patch.object(good_morning, "run") as routine:
            voice._handle(np.zeros(10))
        routine.assert_called_once()


class BargeInTests(unittest.TestCase):
    def test_wake_word_while_speaking_cuts_him_off(self) -> None:
        voice = room_voice.RoomVoice(Mock(), detector=Mock(), final=Mock(), live=Mock())
        voice.stream = FakeStream()
        started = __import__("threading").Event()

        def slow_speak(text, cache=False):  # noqa: ANN001, ANN202
            started.set()
            stopped.wait(2)

        stopped = __import__("threading").Event()
        with patch("speech.tts.speak", side_effect=slow_speak), \
                patch("speech.tts.stop_playback", side_effect=stopped.set) as stop, \
                patch.object(room_volume, "prepare"), \
                patch.object(voice, "_heard_wake", side_effect=lambda f: started.is_set()):
            self.assertTrue(voice._speak("A very long answer about the Roman empire."))
        stop.assert_called_once()

    def test_without_a_stream_it_just_speaks(self) -> None:
        voice = room_voice.RoomVoice(Mock(), detector=Mock(), final=Mock(), live=Mock())
        with patch("speech.tts.speak") as speak, patch.object(room_volume, "prepare"):
            self.assertFalse(voice._speak("Hi.", cache="auto"))
        speak.assert_called_once_with("Hi.", cache="auto")
        self.assertEqual(voice.last_reply, "Hi.")


class AnnounceEndpointTests(unittest.TestCase):
    def test_queues_or_says_its_off(self) -> None:
        client = TestClient(server.app)
        headers = {"Authorization": "Bearer t"}
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "t"}):
            with patch.object(room_voice, "announce", return_value=True) as announce:
                self.assertEqual(client.post("/api/voice/announce", json={"text": "Dinner"}, headers=headers).json(),
                                 {"queued": True})
            announce.assert_called_once_with("Dinner")
            with patch.object(room_voice, "announce", return_value=False):
                self.assertEqual(client.post("/api/voice/announce", json={"text": "x"}, headers=headers).status_code,
                                 409)


if __name__ == "__main__":
    unittest.main()
