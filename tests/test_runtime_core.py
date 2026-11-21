"""The runtime itself: built with a fake brain and no background threads,
then its turns, tasks, announcements, monitor switches and loops."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from brain import event_bus, memory_extractor, runtime as runtime_module, wake_alarm
from brain.runtime import JarvisRuntime
from brain.weather import WeatherSnapshot


class StopAfter:
    """A stop event whose wait() says "stop" after ``n`` waits."""

    def __init__(self, n: int) -> None:
        self.n, self.waits = n, 0

    def is_set(self) -> bool:
        return self.waits >= self.n

    def wait(self, _timeout: float) -> bool:
        self.waits += 1
        return self.waits >= self.n

    def set(self) -> None:
        self.waits = self.n


def make_runtime() -> JarvisRuntime:
    agent = Mock(brain_label="Test brain", last_used_tools=False, spoken_turn=False)
    agent.is_available.return_value = True
    with patch.object(JarvisRuntime, "start_monitors"), patch.object(JarvisRuntime, "_deliver_alert_to_phone"):
        rt = JarvisRuntime(agent=agent)
    return rt


class RuntimeCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        self.rt = make_runtime()
        self.addCleanup(event_bus.reset_for_tests)
        self.addCleanup(memory_extractor.remove_listener, self.rt._on_memory_change)

    def test_built_and_online(self) -> None:
        self.assertTrue(self.rt.brain_online)
        self.assertEqual(self.rt.brain_label, "Test brain")
        self.assertEqual(self.rt.recent_events()[0].title, "Runtime online")

    def test_a_spoken_turn_is_flagged_only_for_that_turn(self) -> None:
        patch.object(runtime_module, "session_exists", return_value=True).start()
        self.addCleanup(patch.stopall)
        seen = []
        self.rt.agent.respond.side_effect = lambda text: seen.append(self.rt.agent.spoken_turn) or "Done."
        self.assertEqual(self.rt.respond("game time", spoken=True), "Done.")
        self.rt.respond("hello")
        self.assertEqual(seen, [True, False])
        self.rt.agent.respond.side_effect = RuntimeError("API down")
        with self.assertRaises(RuntimeError):
            self.rt.respond("x", spoken=True)
        self.assertFalse(self.rt.agent.spoken_turn)  # reset even when the turn fails
        failed = [t for t in self.rt.recent_tasks() if t.state == "failed"]
        self.assertEqual(len(failed), 1)

    def test_tasks_stay_visible_briefly(self) -> None:
        task = self.rt.start_task("Reasoning", "weather")
        self.assertEqual(self.rt.recent_tasks()[0].state, "active")
        self.rt.finish_task(task, detail="Reply ready")
        self.rt.finish_task(999)  # unknown: ignored
        self.assertEqual(self.rt.recent_tasks()[0].state, "complete")
        with patch.object(runtime_module.time, "time", return_value=runtime_module.time.time() + 120):
            self.assertEqual(self.rt.recent_tasks(), [])  # finished ones age out after 90 s

    def test_announcements_ride_the_reminder_path(self) -> None:
        self.rt.announce("  ")
        self.rt.announce("The laundry is done, sir.")
        with patch.object(runtime_module, "pop_expired", return_value=[]), \
                patch.object(runtime_module, "_pop_due_reminders", return_value=[]):
            due = self.rt.pop_due_reminders()
            self.assertEqual([d.message for d in due], ["The laundry is done, sir."])
            self.assertEqual(self.rt.pop_due_reminders(), [])

    def test_alerts_go_to_the_inbox_and_the_bus(self) -> None:
        self.rt.raise_alert("test", "Hello", "World", source="unit", push=False)
        self.assertEqual(self.rt.recent_events()[0].title, "Hello")
        self.assertEqual(event_bus.since(0)[-1].title, "Hello")

    def test_stop_monitors(self) -> None:
        linked = threading.Event()
        with patch("integrations.schwab.linked_event", linked), patch.object(memory_extractor, "stop") as stop:
            self.rt.stop_monitors()
        self.assertTrue(self.rt._stop_monitors.is_set())
        self.assertTrue(linked.is_set())
        stop.assert_called_once()


class MonitorSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        self.rt = make_runtime()
        self.addCleanup(event_bus.reset_for_tests)
        self.addCleanup(memory_extractor.remove_listener, self.rt._on_memory_change)
        self.started = []
        fake_thread = lambda target, args=(), daemon=None: SimpleNamespace(  # noqa: E731
            start=lambda: self.started.append(getattr(target, "__name__", str(target))))
        patch.object(runtime_module.threading, "Thread", side_effect=fake_thread).start()
        patch("integrations.schwab.configured", return_value=False).start()
        patch("integrations.nas_stack.enabled", return_value=False).start()
        patch("integrations.nas_host.monitor_enabled", return_value=False).start()
        patch("integrations.google_calendar.is_configured", return_value=False).start()
        patch("integrations.qbittorrent.is_configured", return_value=False).start()
        patch("integrations.gmail.is_configured", return_value=False).start()
        patch("integrations.temp_4k.configured", return_value=False).start()
        patch.object(runtime_module, "_watched_devices", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_defaults_and_switches(self) -> None:
        with patch.dict(os.environ, {"HOME_MONITOR_ENABLED": "true", "WEATHER_MONITOR_ENABLED": "off",
                                     "PRICE_WATCH_MONITOR_ENABLED": "0", "JARVIS_HEALTHCHECKS_URL": "",
                                     "LIGHT_SYNC": "1"}):
            self.rt.start_monitors()
            self.rt.start_monitors()  # a second call starts nothing twice
        self.assertEqual(sorted(self.started), ["_alert_recheck_loop", "_home_monitor_entry", "_light_sync_loop", "_wake_monitor_loop",
                                                "_watch_monitor_loop", "_watchdog_loop"])

    def test_configured_integrations_get_their_monitors(self) -> None:
        patch("integrations.google_calendar.is_configured", return_value=True).start()
        patch("integrations.qbittorrent.is_configured", return_value=True).start()
        patch("integrations.gmail.is_configured", return_value=True).start()
        patch("integrations.nas_host.monitor_enabled", return_value=True).start()
        patch("integrations.temp_4k.configured", return_value=True).start()
        with patch.dict(os.environ, {"HOME_MONITOR_ENABLED": "no", "WEATHER_MONITOR_ENABLED": "no",
                                     "PRICE_WATCH_MONITOR_ENABLED": "no", "WATCH_MONITOR_ENABLED": "no",
                                     "JARVIS_HEALTHCHECKS_URL": "https://hc", "LIGHT_SYNC": "0"}):
            self.rt.start_monitors()
        self.assertEqual(sorted(self.started), ["_alert_recheck_loop", "_calendar_monitor_loop", "_deadman_loop",
                                                "_download_monitor_loop", "_email_triage_loop",
                                                "_nas_host_monitor_loop", "_temp_4k_monitor_loop",
                                                "_wake_monitor_loop", "_watchdog_loop"])

    def test_monitor_switch_words(self) -> None:
        for value, on in (("true", True), ("ON", True), ("1", True), ("false", False), ("off", False), ("", False)):
            with patch.dict(os.environ, {"X_ENABLED": value}):
                self.assertEqual(runtime_module._monitor_on("X_ENABLED"), on, value)
        os.environ.pop("X_ENABLED", None)
        self.assertTrue(runtime_module._monitor_on("X_ENABLED"))  # unset: on


class MonitorLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        self.rt = make_runtime()
        self.addCleanup(event_bus.reset_for_tests)
        self.addCleanup(memory_extractor.remove_listener, self.rt._on_memory_change)

    def test_boot_raid_alert_is_delivered_after_grace_once(self) -> None:
        import tempfile
        import time
        from pathlib import Path
        from integrations import nas_host
        with tempfile.TemporaryDirectory() as directory:
            watch = nas_host.HostWatch(Path(directory) / "host.json")
            now = time.time()
            snapshot = (True, {"observed_at": now, "host_health": {
                "generated_at": now, "containers": [], "disks": [], "volumes": [], "errors": [],
                "raid": [{"name": "md1", "degraded": True, "slots": "2/1", "members": [], "sync": ""}]}})
            self.rt._stop_monitors = StopAfter(3)
            with patch.object(self.rt, "_in_startup_grace", side_effect=[True, False, False]), \
                    patch("integrations.nas_host.HostWatch", return_value=watch), \
                    patch("integrations.nas_stack.watchdog_status", return_value=snapshot) as poll, \
                    patch.object(self.rt, "raise_alert") as publish:
                self.rt._nas_host_monitor_loop()
            self.assertEqual(poll.call_count, 2)
            publish.assert_called_once()
            self.assertIn("md1", publish.call_args.args[2])

    def test_wake_monitor_rings_the_phone_and_tv(self) -> None:
        self.rt._stop_monitors = StopAfter(1)
        alarm = {"music": "spotify:track:1", "volume": 30, "label": "Back in Black"}
        self.addCleanup(wake_alarm._fired.clear)
        with patch.object(wake_alarm, "due", return_value=alarm):
            self.rt._wake_monitor_loop()
        self.assertEqual(wake_alarm.ringing_state()["detail"], "Back in Black is on the TV.")
        (alert,) = event_bus.since(0)
        self.assertEqual((alert.kind, alert.message), ("wake_up", "Back in Black is on the TV."))
        self.assertEqual(alert.context["music"], "spotify:track:1")
        self.assertEqual(alert.grouping.get("category"), "WAKE_UP")

    def test_wake_monitor_survives_a_broken_schedule(self) -> None:
        self.rt._stop_monitors = StopAfter(1)
        with patch.object(wake_alarm, "due", side_effect=ValueError("bad file")), \
                self.assertLogs("jarvis.brain.runtime", level="ERROR"):
            self.rt._wake_monitor_loop()
        self.assertEqual(event_bus.since(0), [])

    def test_weather_monitor_offline_online_and_big_shifts(self) -> None:
        snaps = iter([
            WeatherSnapshot(online=False, error="DNS"),
            WeatherSnapshot(online=True, location="Herriman", temperature_f=50, description="clear"),
            WeatherSnapshot(online=True, location="Herriman", temperature_f=63, description="clear"),
        ])
        self.rt._stop_monitors = StopAfter(3)
        with patch.object(runtime_module, "get_weather_snapshot", side_effect=lambda: next(snaps)), \
                patch.object(self.rt, "_in_startup_grace", return_value=False):
            self.rt._weather_monitor_loop()
        titles = [e.title for e in self.rt.recent_events()]
        for title in ("Weather monitor offline", "Weather monitor online", "Weather snapshot", "Weather shifted"):
            self.assertIn(title, titles)
        self.assertEqual(self.rt.weather_snapshot().temperature_f, 63)


if __name__ == "__main__":
    unittest.main()
