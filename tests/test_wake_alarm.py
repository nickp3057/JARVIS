"""Wake-up alarm: parsing, scheduling, once-a-day firing and the tools."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from brain import tools, wake_alarm


class WakeAlarmTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(wake_alarm, "_FILE", Path(tmp.name) / "wake_alarm.json")
        p.start()
        self.addCleanup(p.stop)
        wake_alarm._fired.clear()  # an alarm another test fired isn't ringing here

    def test_parsing(self) -> None:
        self.assertEqual(wake_alarm.parse_time("7"), "07:00")
        self.assertEqual(wake_alarm.parse_time("6:45 am"), "06:45")
        self.assertEqual(wake_alarm.parse_time("12:15am"), "00:15")
        self.assertEqual(wake_alarm.parse_time("7:30pm"), "19:30")
        with self.assertRaises(ValueError):
            wake_alarm.parse_time("25:00")
        self.assertEqual(wake_alarm.parse_days("weekdays"), [0, 1, 2, 3, 4])
        self.assertEqual(wake_alarm.parse_days("Mon, Wed and Fri"), [0, 2, 4])
        self.assertEqual(wake_alarm.normalize_music(
            "https://open.spotify.com/playlist/37i9dQZF1DX0UrRvztWcAU?si=abc"),
            "spotify:playlist:37i9dQZF1DX0UrRvztWcAU")
        self.assertEqual(wake_alarm.normalize_music("resume"), "")
        self.assertEqual(wake_alarm.normalize_music("Back in Black AC/DC"), "search:Back in Black AC/DC")
        self.assertEqual(wake_alarm.normalize_music("search:Back in Black"), "search:Back in Black")

    def test_fires_once_in_its_window_on_its_days(self) -> None:
        wake_alarm.set_alarm("7:00am", "weekdays")
        tuesday = datetime(2026, 10, 27, 7, 3)
        self.assertIsNotNone(wake_alarm.due(tuesday))
        self.assertIsNone(wake_alarm.due(tuesday))  # already fired today
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 28, 6, 59)))
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 28, 7, 11)))  # window missed
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 31, 7, 1)))  # Saturday
        self.assertEqual(wake_alarm.next_time(wake_alarm.load(), datetime(2026, 10, 30, 8, 0)),
                         datetime(2026, 11, 2, 7, 0))

    def test_snooze_rings_at_nine_minutes_beside_fired_weekly_alarm(self) -> None:
        now = datetime(2026, 10, 27, 8, 0)
        wake_alarm.set_alarm("8:00", "weekdays")
        self.assertIsNotNone(wake_alarm.due(now))
        wake_alarm.snooze(now=now)
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 27, 8, 8)))
        self.assertEqual(wake_alarm.due(datetime(2026, 10, 27, 8, 9))["time"], "08:09")
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 27, 8, 9, 30)))
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 27, 8, 10)))

    def test_each_day_keeps_its_own_time(self) -> None:
        wake_alarm.set_alarm("6:30am", "weekdays", music="Back in Black")
        wake_alarm.set_alarm("8", "tue,thu")
        wake_alarm.set_alarm("9:15", "saturday", volume=20)
        data = wake_alarm.load()
        self.assertEqual(data["times"], ["06:30", "08:00", "06:30", "08:00", "06:30", "09:15", None])
        self.assertEqual((data["label"], data["volume"]), ("Back in Black", 20))  # music kept
        self.assertIn("6:30 AM Mon, Wed, Fri; 8:00 AM Tue, Thu; 9:15 AM Sat", wake_alarm.describe())
        friday_night = datetime(2026, 10, 30, 22, 0)
        self.assertEqual(wake_alarm.public(friday_night)["time"], "09:15")
        self.assertEqual(wake_alarm.public(friday_night)["days"], "Tomorrow")
        self.assertEqual(wake_alarm.next_time(data, datetime(2026, 10, 31, 10, 0)), datetime(2026, 11, 2, 6, 30))
        self.assertTrue(wake_alarm.cancel("weekends"))
        self.assertIsNone(wake_alarm.load()["times"][5])
        self.assertIsNotNone(wake_alarm.load()["times"][1])

    def test_in_three_minutes_is_a_one_off_beside_the_schedule(self) -> None:
        now = datetime(2026, 10, 27, 18, 55)
        wake_alarm.set_alarm("7:00", "weekdays", music="Morning Acoustic")
        data = wake_alarm.set_alarm("in 3 minutes", music="Back in Black", now=now)
        self.assertEqual(data["once"]["at"], "2026-10-27T18:58")
        self.assertEqual(data["times"][1], "07:00")  # the schedule is untouched
        self.assertEqual(wake_alarm.public(now)["label"], "Back in Black")
        fired = wake_alarm.due(datetime(2026, 10, 27, 18, 58, 10))
        self.assertEqual((fired["music"], fired["label"]), ("search:Back in Black", "Back in Black"))
        self.assertIsNone(wake_alarm.load()["once"])  # gone after it goes off
        self.assertEqual(wake_alarm.public(now)["label"], "Morning Acoustic")
        self.assertEqual(wake_alarm.set_alarm("in 2 hours", now=now)["once"]["at"], "2026-10-27T20:55")
        late = wake_alarm.set_alarm("6:00am", "once", now=now)
        self.assertEqual(late["once"]["at"], "2026-10-28T06:00")  # already past today
        self.assertTrue(wake_alarm.cancel("once"))
        self.assertIsNone(wake_alarm.load()["once"])

    def test_one_day_changes_then_back_to_normal(self) -> None:
        tuesday_night = datetime(2026, 10, 27, 22, 0)
        wake_alarm.set_alarm("6:30am", "weekdays")
        with patch.object(wake_alarm, "datetime", wraps=datetime) as fake:
            fake.now.return_value = tuesday_night
            wake_alarm.set_alarm("8:30", "tomorrow", now=tuesday_night)
            self.assertEqual(wake_alarm.public(tuesday_night)["time"], "08:30")
            self.assertIsNone(wake_alarm.due(datetime(2026, 10, 28, 6, 31)))  # not at 6:30
            self.assertIsNotNone(wake_alarm.due(datetime(2026, 10, 28, 8, 31)))
            self.assertIn("Wed Oct 28: 8:30 AM instead", wake_alarm.describe())
            # The Wednesday after rings at the usual time.
            self.assertEqual(wake_alarm.next_time(wake_alarm.load(), datetime(2026, 11, 3, 22, 0)),
                             datetime(2026, 11, 4, 6, 30))
            wake_alarm.skip("2026-10-29", now=tuesday_night)
            self.assertEqual(wake_alarm.next_time(wake_alarm.load(), datetime(2026, 10, 28, 9, 0)),
                             datetime(2026, 10, 30, 6, 30))  # Thursday skipped
            wake_alarm.skip("2026-10-29", restore=True, now=tuesday_night)
            self.assertEqual(wake_alarm.next_time(wake_alarm.load(), datetime(2026, 10, 28, 9, 0)),
                             datetime(2026, 10, 29, 6, 30))
            self.assertEqual(wake_alarm.load()["times"][2], "06:30")  # the schedule itself never moved
        with self.assertRaises(ValueError):
            wake_alarm.skip("someday", now=tuesday_night)

    def test_phone_snooze_rings_again_and_im_up_cancels_it(self) -> None:
        now = datetime(2026, 10, 28, 6, 31, 20)
        at = wake_alarm.snooze(now)
        self.assertEqual(at, datetime(2026, 10, 28, 6, 40))
        fired = wake_alarm.due(datetime(2026, 10, 28, 6, 40, 5))
        self.assertEqual(fired["music"], "")  # resumes where it paused
        wake_alarm.snooze(now)
        wake_alarm.awake()
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 28, 6, 40, 5)))

    def test_paused_schedule_and_old_file_migrate(self) -> None:
        wake_alarm._FILE.write_text(json.dumps({
            "time": "06:45", "days": [0, 1, 2, 3, 4], "once": False, "music": "search:Back in Black",
            "label": "Back in Black", "volume": 30, "enabled": True, "last_fired": "2026-10-27"}))
        data = wake_alarm.load()
        self.assertEqual(data["times"], ["06:45"] * 5 + [None, None])
        self.assertEqual(data["label"], "Back in Black")
        wake_alarm.set_schedule(data["times"], "resume", enabled=False)
        self.assertIsNone(wake_alarm.public(datetime(2026, 10, 27, 5, 0)))
        self.assertIsNone(wake_alarm.due(datetime(2026, 10, 27, 6, 46)))
        self.assertIn("(paused)", wake_alarm.describe())

    def test_tools(self) -> None:
        reply = tools.execute_tool("set_wake_up", {
            "time": "6:30am", "days": "weekdays", "label": "Morning Acoustic",
            "music": "https://open.spotify.com/playlist/37i9dQZF1DX0UrRvztWcAU"})
        self.assertIn("6:30 AM weekdays, with Morning Acoustic", reply)
        self.assertIn("with chill vibes", tools.execute_tool("set_wake_up", {"time": "7", "music": "chill vibes"}))
        self.assertIn("cancelled for sunday", tools.execute_tool("cancel_wake_up", {"days": "sunday"}))
        self.assertIn("cancelled", tools.execute_tool("cancel_wake_up", {}))
        self.assertIsNone(wake_alarm.public())

    def test_snooze_keeps_a_pending_one_off(self) -> None:
        now = datetime(2026, 10, 28, 6, 31)
        wake_alarm.set_alarm("10:00", "once", music="Back in Black", now=now)
        wake_alarm.snooze(now)
        self.assertEqual(wake_alarm.load()["once"]["at"], "2026-10-28T10:00")  # not overwritten
        self.assertEqual(wake_alarm.due(datetime(2026, 10, 28, 6, 40, 5))["music"], "")  # the snooze rings
        fired = wake_alarm.due(datetime(2026, 10, 28, 10, 0, 5))
        self.assertEqual(fired["music"], "search:Back in Black")  # and the one-off still does

    def test_old_snooze_in_once_is_read(self) -> None:
        wake_alarm._FILE.write_text(json.dumps({
            "times": [None] * 7, "music": "", "label": "", "volume": 30, "enabled": True,
            "once": {"at": "2026-10-28T06:40", "music": "", "label": "", "snooze": True},
            "overrides": {}, "last_fired": ""}))
        data = wake_alarm.load()
        self.assertIsNone(data["once"])
        self.assertEqual(data["snooze"], {"at": "2026-10-28T06:40"})
        self.assertIsNotNone(wake_alarm.due(datetime(2026, 10, 28, 6, 40, 5)))

    def test_concurrent_snoozes_and_dues_keep_the_file_whole(self) -> None:
        import threading
        wake_alarm.set_alarm("7:00", "weekdays")
        now = datetime(2026, 10, 28, 6, 31)
        threads = [threading.Thread(target=wake_alarm.snooze, args=(now,)) for _ in range(10)]
        threads += [threading.Thread(target=wake_alarm.due, args=(now,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = wake_alarm.load()
        self.assertEqual(data["times"][2], "07:00")
        self.assertEqual(data["snooze"], {"at": "2026-10-28T06:40"})


class GoodMorningTests(unittest.TestCase):
    STATES = [
        {"entity_id": "light.desk_lights", "state": "off"},
        {"entity_id": "light.desk_lights_segment_1", "state": "on"},
        {"entity_id": "light.old_lamp", "state": "unavailable"},
        {"entity_id": "light.bedroom_lights", "state": "off"},
        {"entity_id": "switch.fan", "state": "off"},
    ]

    def test_every_light_but_segments(self) -> None:
        self.assertEqual(wake_alarm.good_morning_targets(self.STATES),
                         ["light.bedroom_lights", "light.desk_lights"])

    def test_lights_on_by_default(self) -> None:
        with patch.dict("os.environ", {"WAKE_UP_SCENE": "", "WAKE_UP_LIGHTS": ""}), \
                patch("integrations.home_assistant.get_states", return_value=(True, self.STATES)), \
                patch("integrations.home_assistant.call_service", return_value=(True, "ok")) as call:
            self.assertTrue(wake_alarm.good_morning()[0])
        call.assert_called_once_with("light", "turn_on",
                                     service_data={"entity_id": ["light.bedroom_lights", "light.desk_lights"]})

    def test_good_morning_scene_when_ha_has_one(self) -> None:
        states = self.STATES + [{"entity_id": "scene.good_morning", "state": "unknown"}]
        with patch.dict("os.environ", {"WAKE_UP_SCENE": "", "WAKE_UP_LIGHTS": ""}), \
                patch("integrations.home_assistant.get_states", return_value=(True, states)), \
                patch("integrations.home_assistant.call_service", return_value=(True, "ok")) as call:
            wake_alarm.good_morning()
        call.assert_called_once_with("scene", "turn_on", "scene.good_morning")

    def test_scene_wins_and_off_switch(self) -> None:
        with patch.dict("os.environ", {"WAKE_UP_SCENE": "scene.good_morning"}), \
                patch("integrations.home_assistant.call_service", return_value=(True, "ok")) as call:
            wake_alarm.good_morning()
        call.assert_called_once_with("scene", "turn_on", "scene.good_morning")
        with patch.dict("os.environ", {"WAKE_UP_LIGHTS": "off"}), \
                patch("integrations.home_assistant.call_service") as call:
            wake_alarm.good_morning()
        call.assert_not_called()


class GrantScopeTests(unittest.TestCase):
    def test_grant_keeps_token(self) -> None:
        from app import client_tokens
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(client_tokens, "_FILE", Path(tmp) / "tokens.json"):
            token = client_tokens.create("tv", ["events:read"])
            self.assertTrue(client_tokens.grant("tv", ["wake:write"]))
            self.assertEqual(client_tokens.scopes_for(token),
                             ("tv", frozenset({"events:read", "wake:write"})))
            self.assertFalse(client_tokens.grant("nobody", ["wake:write"]))
            with self.assertRaises(ValueError):
                client_tokens.grant("tv", ["root"])


class WakeBackupRingTests(unittest.TestCase):
    """The TV can't hear the alarm in deep standby: wake it, else ring the room."""

    def setUp(self) -> None:
        wake_alarm.stop_ring()
        wake_alarm.alarm_fired()
        self.addCleanup(wake_alarm.stop_ring)
        self.addCleanup(wake_alarm._fired.clear)
        self.stop = __import__("threading").Event()
        env = patch.dict("os.environ", {"WAKE_TV_ACK_S": "10"})
        env.start()
        self.addCleanup(env.stop)
        # Don't actually wait out the delay.
        wait = patch.object(self.stop, "wait", return_value=False)
        wait.start()
        self.addCleanup(wait.stop)

    def test_rings_when_tv_never_confirms(self) -> None:
        from unittest.mock import Mock
        resend = Mock()
        with patch("integrations.tv_remote.power", return_value=(True, "ok")) as power:
            self.assertEqual(wake_alarm.wake_tv_with_backup(self.stop, resend), "ringing")
        # On isn't enough (2026-11-19): powered on twice, resent once, then the room rings.
        self.assertEqual(power.call_count, 2)
        resend.assert_called_once()
        self.assertTrue(wake_alarm.ringing())

    def test_quiet_when_tv_confirms(self) -> None:
        self.assertTrue(wake_alarm.tv_acknowledged())
        with patch("integrations.tv_remote.power", return_value=(True, "ok")) as power:
            self.assertEqual(wake_alarm.wake_tv_with_backup(self.stop), "tv")
        power.assert_called_once_with(True)
        self.assertFalse(wake_alarm.ringing())

    def test_resent_alarm_reaches_tv(self) -> None:
        with patch("integrations.tv_remote.power", return_value=(True, "ok")):
            outcome = wake_alarm.wake_tv_with_backup(self.stop, wake_alarm.tv_acknowledged)
        self.assertEqual(outcome, "tv-resent")
        self.assertFalse(wake_alarm.ringing())

    def test_ack_without_an_alarm_is_ignored(self) -> None:
        wake_alarm._fired.clear()
        self.assertFalse(wake_alarm.tv_acknowledged())

    def test_quiet_when_already_answered_on_phone(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(wake_alarm, "_FILE", Path(tmp.name) / "w.json"):
            wake_alarm.awake()
        with patch("integrations.tv_remote.power", return_value=(True, "ok")):
            self.assertEqual(wake_alarm.wake_tv_with_backup(self.stop), "answered")
        self.assertFalse(wake_alarm.ringing())

    def test_snooze_or_im_up_silences_the_ring(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertTrue(wake_alarm.start_backup_ring())
        self.assertTrue(wake_alarm.ringing())
        with patch.object(wake_alarm, "_FILE", Path(tmp.name) / "w.json"):
            wake_alarm.snooze(now=datetime(2026, 10, 31, 9, 0))
        self.assertFalse(wake_alarm.ringing())
        # The snooze rings again later: a fresh alarm can ring the room again.
        wake_alarm.alarm_fired()
        self.assertTrue(wake_alarm.start_backup_ring())

    def test_ring_gives_up_after_its_limit(self) -> None:
        with patch.dict("os.environ", {"WAKE_BACKUP_RING_S": "60"}):
            wake_alarm.start_backup_ring(now=1000.0)
        self.assertTrue(wake_alarm.ringing(now=1059.0))
        self.assertFalse(wake_alarm.ringing(now=1061.0))

    def test_ringing_state_until_answered_snoozed_or_stale(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(wake_alarm._fired.clear)
        with patch.object(wake_alarm, "_FILE", Path(tmp.name) / "w.json"):
            wake_alarm.alarm_fired("Back in Black is on the TV.", now=1000.0)
            state = wake_alarm.ringing_state(now=1010.0)
            self.assertEqual((state["state"], state["since"], state["detail"]),
                             ("ringing", 1000.0, "Back in Black is on the TV."))
            self.assertIsNone(wake_alarm.ringing_state(now=1000.0 + wake_alarm.RINGING_WINDOW_S + 1))
            at = wake_alarm.snooze(now=datetime.fromtimestamp(1010.0))
            snoozed = wake_alarm.ringing_state(now=1020.0)
            self.assertEqual((snoozed["state"], snoozed["until"]), ("snoozed", at.timestamp()))
            with patch.object(wake_alarm, "_good_morning_logged"), patch.object(wake_alarm, "_pause_tv") as pause, \
                    patch("brain.event_bus.publish"):
                wake_alarm.im_up()
            self.assertIsNone(wake_alarm.ringing_state(now=1030.0))

    def test_im_awake_leave_the_lights_skips_good_morning(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(wake_alarm._fired.clear)
        with patch.object(wake_alarm, "_FILE", Path(tmp.name) / "w.json"), \
                patch.object(wake_alarm, "_good_morning_logged") as lights, \
                patch.object(wake_alarm, "_pause_tv") as pause, patch("brain.event_bus.publish"):
            wake_alarm.alarm_fired("x")
            wake_alarm.im_up(lights=False)
            __import__("time").sleep(0.2)
            lights.assert_not_called()
            pause.assert_called_once()  # the alarm itself (the TV's music) still stops
            wake_alarm.im_up()
            __import__("time").sleep(0.2)
            lights.assert_called_once()

    def test_im_up_pauses_the_tv_only_after_an_alarm(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(wake_alarm._fired.clear)
        calls = []
        with patch.object(wake_alarm, "_FILE", Path(tmp.name) / "w.json"), \
                patch.object(wake_alarm, "_good_morning_logged"), patch("brain.event_bus.publish"), \
                patch("integrations.home_assistant.call_service", side_effect=lambda *a, **k: calls.append(a) or (True, "")):
            wake_alarm._fired.clear()
            wake_alarm.im_up()
            wake_alarm.alarm_fired("x")
            wake_alarm.im_up()
            __import__("time").sleep(0.2)
        self.assertEqual(calls, [("remote", "send_command", __import__("integrations.tv_remote").tv_remote.entity(),
                                  {"command": "MEDIA_PAUSE"})])

    def test_tv_is_on_reads_just_the_remote_entity(self) -> None:
        with patch("integrations.home_assistant._request", return_value=(True, {"state": "on"})) as req, \
                patch.dict("os.environ", {"TV_REMOTE_ENTITY": "remote.nick_s_room_tv"}):
            self.assertTrue(wake_alarm.tv_is_on())
        req.assert_called_once_with("GET", "/api/states/remote.nick_s_room_tv")
        with patch("integrations.home_assistant._request", return_value=(False, "unavailable")):
            self.assertFalse(wake_alarm.tv_is_on())


if __name__ == "__main__":
    unittest.main()
