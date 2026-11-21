import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brain import watchdog
from brain.watchdog import Action, MonitorWatch, Supervised, SystemdWatchdog


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _dead():
    return SimpleNamespace(is_alive=lambda: False, ident=None)


class MonitorWatchTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.watch = MonitorWatch(self.clock)

    def test_a_dead_monitor_is_restarted_then_given_up_on(self):
        entry = {"arr_stack_monitor_loop": Supervised(lambda: None, _dead(), None)}
        with patch.dict(os.environ, {"MONITOR_RESTART_MAX": "3"}):
            kinds = []
            for _ in range(5):
                kinds += [a.kind for a in self.watch.check(entry, {})]
                self.clock.now += 60
        self.assertEqual(kinds, ["restart", "restart", "restart", "gave_up"])
        # An hour later it may try again.
        self.clock.now += 3600
        entry = {"arr_stack_monitor_loop": Supervised(lambda: None, SimpleNamespace(is_alive=lambda: True, ident=None), None)}
        self.watch.check(entry, {})
        entry = {"arr_stack_monitor_loop": Supervised(lambda: None, _dead(), None)}
        self.assertEqual([a.kind for a in self.watch.check(entry, {})], ["restart"])

    def test_a_frozen_thread_is_reported_once_and_cleared_when_it_moves(self):
        release = threading.Event()
        spinning = threading.Event()

        def busy():
            spinning.set()
            while not release.is_set():
                pass  # a tight loop on one line: no wait, no sleep

        thread = threading.Thread(target=busy, daemon=True)
        thread.start()
        spinning.wait(2)
        self.addCleanup(release.set)
        entry = {"busy": Supervised(busy, thread, None)}
        with patch.dict(os.environ, {"MONITOR_HANG_S": "60"}):
            self.assertEqual(self.watch.check(entry, sys._current_frames()), [])
            self.clock.now += 120
            # The spin alternates between two bytecode lines at most; pin it.
            with patch.object(watchdog, "_location", return_value=("x.py", 3, "busy")):
                self.watch.check(entry, sys._current_frames())
                self.clock.now += 120
                actions = self.watch.check(entry, sys._current_frames())
                self.clock.now += 120
                again = self.watch.check(entry, sys._current_frames())
            self.assertEqual([a.kind for a in actions], ["stuck"])
            self.assertIn("x.py:3", actions[0].detail)
            self.assertEqual(again, [])
            with patch.object(watchdog, "_location", return_value=None):
                self.assertEqual([a.kind for a in self.watch.check(entry, sys._current_frames())], ["unstuck"])

    def test_a_waiting_thread_is_idle_not_stuck(self):
        stop = threading.Event()
        thread = threading.Thread(target=lambda: stop.wait(30), daemon=True)
        thread.start()
        self.addCleanup(stop.set)
        time.sleep(0.05)
        frame = sys._current_frames()[thread.ident]
        self.assertIsNone(watchdog._location(frame))


class SystemdWatchdogTests(unittest.TestCase):
    def test_inert_without_systemd(self):
        with patch.dict(os.environ, {"NOTIFY_SOCKET": "", "WATCHDOG_USEC": ""}):
            self.assertFalse(SystemdWatchdog(lambda: True).start())

    def test_pings_only_while_healthy(self):
        tmp = tempfile.mkdtemp()
        path = str(Path(tmp) / "notify.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(path)
        server.settimeout(2)
        self.addCleanup(server.close)
        healthy = {"ok": True}
        with patch.dict(os.environ, {"NOTIFY_SOCKET": path, "WATCHDOG_USEC": "300000"}):  # ping every 0.1 s
            dog = SystemdWatchdog(lambda: healthy["ok"])
            self.assertTrue(dog.start())
            self.addCleanup(dog.stop)
            self.assertEqual(server.recv(64), b"READY=1")
            self.assertEqual(server.recv(64), b"WATCHDOG=1")
            healthy["ok"] = False
            time.sleep(0.15)
            while True:  # drain anything sent before the flip
                server.settimeout(0.3)
                try:
                    server.recv(64)
                except socket.timeout:
                    break
            server.settimeout(0.4)
            with self.assertRaises(socket.timeout):
                server.recv(64)


class RuntimeWatchdogTests(unittest.TestCase):
    def test_restart_respawns_and_keeps_the_mirror_attribute(self):
        from brain.runtime import JarvisRuntime
        rt = JarvisRuntime.__new__(JarvisRuntime)
        rt.record_event = MagicMock()
        rt.raise_alert = MagicMock()
        ran = threading.Event()
        old = _dead()
        entry = Supervised(ran.set, old, "_arr_monitor_thread")
        with patch("brain.tool_health.record") as record:
            rt._watchdog_act(Action("restart", "arr_stack_monitor_loop"), entry)
        self.assertTrue(ran.wait(2))
        self.assertIsNot(rt._arr_monitor_thread, old)
        self.assertIn("arr_stack_monitor_loop", rt._supervised)
        record.assert_called_once()
        self.assertEqual(rt.record_event.call_args.kwargs["title"], "Monitor restarted")
        with patch("brain.tool_health.record"):
            rt._watchdog_act(Action("gave_up", "arr_stack_monitor_loop", "died 4 times in an hour"), entry)
        self.assertIn("keeps stopping", rt.raise_alert.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
