import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from brain import notifications

TOKEN = "a" * 64


class NotificationRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        file_patch = patch.object(notifications, "_DEVICES_FILE", Path(self.tmp.name) / "devices.json")
        file_patch.start()
        self.addCleanup(file_patch.stop)
        env = patch.dict(os.environ, {"APNS_ENABLED": "", "JARVIS_NOTIFY_HA_SERVICE": "", "ARR_STACK_NOTIFY_SERVICE": "notify.mobile_app_phone"})
        env.start()
        self.addCleanup(env.stop)

    def test_app_push_is_stubbed_so_home_assistant_is_primary(self):
        with patch("integrations.home_assistant.call_service", return_value=(True, "ok")) as call:
            delivery = notifications.send("T", "hello")
        self.assertEqual(delivery, notifications.Delivery(True, "home_assistant", "sent"))
        self.assertEqual(call.call_args.args, ("notify", "mobile_app_phone"))
        self.assertEqual(notifications.status()["primary"], "home_assistant")

    def test_ready_app_push_failure_falls_back_to_home_assistant(self):
        with patch.object(notifications, "app_push_status", return_value=(True, "")), \
             patch.object(notifications, "_send_apns", return_value=(False, "410")) as apns, \
             patch("integrations.home_assistant.call_service", return_value=(True, "ok")):
            delivery = notifications.send("T", "hello")
        apns.assert_called_once()
        self.assertEqual(delivery.channel, "home_assistant")

    def test_ready_app_push_success_skips_home_assistant(self):
        with patch.object(notifications, "app_push_status", return_value=(True, "")), \
             patch.object(notifications, "_send_apns", return_value=(True, "sent")), \
             patch("integrations.home_assistant.call_service") as ha:
            delivery = notifications.send("T", "hello")
        self.assertEqual(delivery.channel, "app_push")
        ha.assert_not_called()

    def test_all_channels_failing_reports_every_reason(self):
        with patch("integrations.home_assistant.call_service", return_value=(False, "HA down")):
            delivery = notifications.send("T", "hello")
        self.assertFalse(delivery.delivered)
        self.assertIn("HA down", delivery.detail)
        self.assertIn("app push skipped", delivery.detail)

    def test_home_assistant_exception_never_escapes(self):
        with patch("integrations.home_assistant.call_service", side_effect=RuntimeError("boom")):
            self.assertFalse(notifications.send("T", "hello").delivered)

    def test_new_service_name_wins_and_non_mobile_services_are_refused(self):
        with patch.dict(os.environ, {"JARVIS_NOTIFY_HA_SERVICE": "notify.mobile_app_new"}):
            self.assertEqual(notifications.ha_service(), "mobile_app_new")
        with patch.dict(os.environ, {"JARVIS_NOTIFY_HA_SERVICE": "notify.persistent_notification", "ARR_STACK_NOTIFY_SERVICE": ""}):
            self.assertEqual(notifications.ha_service(), "")
            self.assertFalse(notifications.configured())

    def test_app_push_status_names_first_missing_piece(self):
        self.assertIn("APNS_ENABLED", notifications.app_push_status()[1])
        with patch.dict(os.environ, {"APNS_ENABLED": "true", "APNS_KEY_PATH": "", "APNS_KEY_ID": "K"}):
            self.assertIn("APNS_KEY_PATH", notifications.app_push_status()[1])

    def test_device_registry_round_trip(self):
        self.assertTrue(notifications.register_device(TOKEN, "sandbox", "iPhone")[0])
        self.assertFalse(notifications.register_device("nothex")[0])
        self.assertEqual(notifications.device_count(), 1)
        self.assertTrue(notifications.unregister_device(TOKEN.upper()))
        self.assertEqual(notifications.device_count(), 0)


class PushEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(notifications, "_DEVICES_FILE", Path(self.tmp.name) / "devices.json"),
                  patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token"})):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer test-token"}

    def test_register_status_and_unregister(self):
        response = self.client.post("/api/push/register", json={"device_token": TOKEN, "environment": "sandbox"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["app_push"]["devices"], 1)
        self.assertEqual(self.client.get("/api/push/status", headers=self.headers).json()["app_push"]["devices"], 1)
        self.assertEqual(self.client.delete(f"/api/push/register/{TOKEN}", headers=self.headers).status_code, 204)
        self.assertEqual(notifications.device_count(), 0)

    def test_requires_auth_and_valid_token(self):
        self.assertEqual(self.client.get("/api/push/status").status_code, 401)
        bad = self.client.post("/api/push/register", json={"device_token": "z" * 64}, headers=self.headers)
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
