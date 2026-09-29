import os
import time
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


class ApnsSenderTests(unittest.TestCase):
    def setUp(self):
        import httpx
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.key_path = root / "AuthKey_TEST.p8"
        self.key_path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        for p in (patch.object(notifications, "_DEVICES_FILE", root / "devices.json"),
                  patch.dict(os.environ, {"APNS_ENABLED": "true", "APNS_KEY_PATH": str(self.key_path), "APNS_KEY_ID": "KEYID12345",
                                          "APNS_TEAM_ID": "TEAMID1234", "APNS_BUNDLE_ID": "com.nick.jarviscompanion"})):
            p.start()
            self.addCleanup(p.stop)
        notifications._jwt_cache.update(token="", issued=0.0, fingerprint="")
        self.requests = []
        self.responses = {}

        def handler(request):
            self.requests.append(request)
            device = request.url.path.rsplit("/", 1)[1]
            status, reason = self.responses.get((request.url.host, device), (200, ""))
            if callable(status):
                status, reason = status()
            return httpx.Response(status, json={"reason": reason} if reason else None)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        patcher = patch.object(notifications, "_http_client", client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def verify_jwt(self, token):
        import base64, json as js
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        header, claims, signature = token.split(".")
        raw = pad(signature)
        self.assertEqual(len(raw), 64)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        self.key.public_key().verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
        return js.loads(pad(header)), js.loads(pad(claims))

    def test_provider_token_is_valid_es256_and_cached(self):
        config = notifications._apns_config()
        token = notifications._provider_token(config)
        header, claims = self.verify_jwt(token)
        self.assertEqual(header, {"alg": "ES256", "kid": "KEYID12345"})
        self.assertEqual(claims["iss"], "TEAMID1234")
        self.assertEqual(notifications._provider_token(config), token)
        with patch.object(notifications.time, "time", return_value=time.time() + 51 * 60):
            self.assertNotEqual(notifications._provider_token(config), token)

    def test_ready_when_configured_and_registered(self):
        self.assertEqual(notifications.app_push_status(), (False, "No companion device has registered a push token"))
        notifications.register_device(TOKEN, "sandbox")
        self.assertEqual(notifications.app_push_status(), (True, "ready"))
        self.assertEqual(notifications.status()["primary"], "app_push")

    def test_send_uses_apple_headers_and_device_environment(self):
        notifications.register_device(TOKEN, "sandbox")
        delivery = notifications.send("Jarvis — arr stack", "Radarr down")
        self.assertEqual(delivery.channel, "app_push")
        [request] = self.requests
        self.assertEqual(request.url.host, "api.sandbox.push.apple.com")
        self.assertEqual(request.url.path, f"/3/device/{TOKEN}")
        self.assertEqual(request.headers["apns-topic"], "com.nick.jarviscompanion")
        self.assertEqual(request.headers["apns-push-type"], "alert")
        self.verify_jwt(request.headers["authorization"].removeprefix("bearer "))
        body = __import__("json").loads(request.content)
        self.assertEqual(body["aps"]["alert"], {"title": "Jarvis — arr stack", "body": "Radarr down"})

    def test_incident_grouping_sets_thread_collapse_and_passive(self):
        notifications.register_device(TOKEN, "sandbox")
        notifications.send("T", "loud", thread_id="arr-stack-abc", collapse_id="arr-stack-abc")
        notifications.send("T", "quiet", thread_id="arr-stack-abc", collapse_id="arr-stack-abc", passive=True)
        loud, quiet = self.requests
        loud_body, quiet_body = (__import__("json").loads(r.content)["aps"] for r in (loud, quiet))
        self.assertEqual(loud.headers["apns-collapse-id"], "arr-stack-abc")
        self.assertEqual(loud_body["thread-id"], "arr-stack-abc")
        self.assertEqual((loud.headers["apns-priority"], loud_body["sound"]), ("10", "default"))
        self.assertNotIn("interruption-level", loud_body)
        self.assertEqual((quiet.headers["apns-priority"], quiet_body["interruption-level"]), ("5", "passive"))
        self.assertNotIn("sound", quiet_body)
        # Ungrouped sends keep the old payload exactly.
        notifications.send("T", "plain")
        self.assertNotIn("apns-collapse-id", self.requests[-1].headers)
        self.assertNotIn("thread-id", __import__("json").loads(self.requests[-1].content)["aps"])

    def test_ha_fallback_carries_group_and_tag(self):
        with patch.dict(os.environ, {"ARR_STACK_NOTIFY_SERVICE": "notify.mobile_app_phone"}), \
             patch("integrations.home_assistant.call_service", return_value=(True, "ok")) as call:
            notifications.send("T", "m", thread_id="arr-stack-abc", collapse_id="arr-stack-abc", passive=True)
        data = call.call_args.kwargs["service_data"]["data"]
        self.assertEqual(data, {"group": "arr-stack-abc", "tag": "arr-stack-abc", "push": {"interruption-level": "passive"}})

    def test_unregistered_device_is_pruned_and_ha_fallback_used(self):
        notifications.register_device(TOKEN, "production")
        self.responses[("api.push.apple.com", TOKEN)] = (410, "Unregistered")
        with patch.dict(os.environ, {"ARR_STACK_NOTIFY_SERVICE": "notify.mobile_app_phone"}), \
             patch("integrations.home_assistant.call_service", return_value=(True, "ok")):
            delivery = notifications.send("T", "m")
        self.assertEqual(delivery.channel, "home_assistant")
        self.assertEqual(notifications.device_count(), 0)

    def test_wrong_environment_is_retried_once_and_remembered(self):
        notifications.register_device(TOKEN, "production")
        self.responses[("api.push.apple.com", TOKEN)] = (400, "BadDeviceToken")
        self.assertTrue(notifications.send("T", "m").delivered)
        self.assertEqual([r.url.host for r in self.requests], ["api.push.apple.com", "api.sandbox.push.apple.com"])
        self.assertEqual(notifications._load_devices()[TOKEN]["environment"], "sandbox")

    def test_expired_provider_token_is_refreshed_once(self):
        notifications.register_device(TOKEN, "sandbox")
        answers = iter([(403, "ExpiredProviderToken"), (200, "")])
        self.responses[("api.sandbox.push.apple.com", TOKEN)] = (lambda: next(answers), None)
        self.assertTrue(notifications.send("T", "m").delivered)
        self.assertEqual(len(self.requests), 2)

    def test_one_good_device_is_enough(self):
        other = "b" * 64
        notifications.register_device(TOKEN, "sandbox")
        notifications.register_device(other, "sandbox")
        self.responses[("api.sandbox.push.apple.com", other)] = (500, "InternalServerError")
        ok, detail = notifications._send_apns("T", "m")
        self.assertTrue(ok)
        self.assertEqual(notifications.device_count(), 2)

    def test_bad_key_file_reports_not_ready(self):
        self.key_path.write_text("not a key")
        ready, reason = notifications.app_push_status()
        self.assertFalse(ready)
        self.assertIn("APNS_KEY_PATH", reason)
