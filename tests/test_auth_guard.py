import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
from app import auth_guard
from brain import away_access


class LockoutTests(unittest.TestCase):
    def setUp(self) -> None:
        auth_guard.reset_for_tests()
        self.env = patch.dict(os.environ, {"JARVIS_AUTH_LOCKOUT_FAILURES": "3", "JARVIS_AUTH_LOCKOUT_WINDOW_S": "600",
                                           "JARVIS_AUTH_LOCKOUT_S": "900"})
        self.env.start()
        self.alerts = []
        self.old_hook = auth_guard.on_lockout
        auth_guard.on_lockout = lambda ip, count: self.alerts.append((ip, count))

    def tearDown(self) -> None:
        auth_guard.on_lockout = self.old_hook
        self.env.stop()
        auth_guard.reset_for_tests()

    def test_blocks_after_limit_inside_window_and_alerts_once(self) -> None:
        self.assertFalse(auth_guard.record_failure("1.2.3.4", now=0))
        self.assertFalse(auth_guard.record_failure("1.2.3.4", now=10))
        self.assertTrue(auth_guard.record_failure("1.2.3.4", now=20))
        self.assertTrue(auth_guard.blocked("1.2.3.4", now=21))
        self.assertFalse(auth_guard.blocked("5.6.7.8", now=21))
        self.assertFalse(auth_guard.blocked("1.2.3.4", now=20 + 901))
        for t in (1000, 1001, 1002):
            auth_guard.record_failure("1.2.3.4", now=t)
        self.assertEqual(self.alerts, [("1.2.3.4", 3)])  # one alert a day per address

    def test_old_failures_age_out(self) -> None:
        auth_guard.record_failure("1.2.3.4", now=0)
        auth_guard.record_failure("1.2.3.4", now=10)
        self.assertFalse(auth_guard.record_failure("1.2.3.4", now=700))
        self.assertFalse(auth_guard.blocked("1.2.3.4", now=701))

    def test_zero_turns_it_off(self) -> None:
        with patch.dict(os.environ, {"JARVIS_AUTH_LOCKOUT_FAILURES": "0"}):
            for t in range(50):
                self.assertFalse(auth_guard.record_failure("1.2.3.4", now=t))

    def test_server_locks_out_guesser_but_never_a_valid_token(self) -> None:
        class FakeRuntime:
            brain_online = True
            brain_label = "Test"
        server._runtime = FakeRuntime()
        try:
            client = TestClient(server.app, base_url="http://localhost")
            with patch.dict(os.environ, {"JARVIS_API_TOKEN": "right"}):
                wrong = {"Authorization": "Bearer wrong"}
                self.assertEqual(client.get("/health", headers=wrong).status_code, 401)
                self.assertEqual(client.get("/health", headers=wrong).status_code, 401)
                self.assertEqual(client.get("/api/glance", headers=wrong).status_code, 429)  # scoped route counts too
                self.assertEqual(client.get("/health", headers=wrong).status_code, 429)
                self.assertEqual(client.get("/health", headers={"Authorization": "Bearer right"}).status_code, 200)
                self.assertEqual(len(self.alerts), 1)
        finally:
            server._runtime = None


class AwayWatchTests(unittest.TestCase):
    def test_two_failures_alert_once_and_success_clears(self) -> None:
        watch = away_access.AwayWatch()
        self.assertIsNone(watch.step(False))
        self.assertEqual(watch.step(False), "down")
        self.assertIsNone(watch.step(False))
        self.assertEqual(watch.step(True), "up")
        self.assertIsNone(watch.step(True))

    def test_not_configured_without_cert(self) -> None:
        with patch.dict(os.environ, {"JARVIS_AWAY_CERT": "", "JARVIS_AWAY_KEY": ""}):
            self.assertFalse(away_access.configured())
            self.assertFalse(away_access.check()[0])


if __name__ == "__main__":
    unittest.main()
