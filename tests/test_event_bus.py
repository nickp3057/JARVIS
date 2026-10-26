"""Alert bus, phone subscriber, scoped client tokens and the SSE stream."""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import app.server as server
from app import client_tokens
from brain import event_bus
from brain.notifications import Delivery
from brain.runtime import JarvisRuntime


class BusTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()

    def test_publish_reaches_every_subscriber_even_if_one_breaks(self) -> None:
        good = Mock()
        event_bus.subscribe("bad", Mock(side_effect=RuntimeError("down")))
        event_bus.subscribe("good", good)
        alert = event_bus.publish("download_stalled", "Download stalled", "Dune", source="downloads")
        good.assert_called_once_with(alert)

    def test_dedupe_within_cooldown(self) -> None:
        first = event_bus.publish("x", "t", "m", source="s", dedupe_key="k", cooldown_s=60)
        second = event_bus.publish("x", "t", "m", source="s", dedupe_key="k", cooldown_s=60)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_since_replays_newer_alerts(self) -> None:
        for n in range(3):
            event_bus.publish("x", f"t{n}", "m", source="s")
        self.assertEqual([a.title for a in event_bus.since(1)], ["t1", "t2"])


class PhoneSubscriberTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        self.runtime = object.__new__(JarvisRuntime)
        self.runtime.record_event = Mock()
        event_bus.subscribe("phone", self.runtime._deliver_alert_to_phone)

    def test_push_to_phone_keeps_incident_grouping(self) -> None:
        with patch("brain.notifications.configured", return_value=True), \
             patch("brain.notifications.send", return_value=Delivery(True, "app_push", "ok")) as send:
            self.runtime.push_to_phone("Arr", "qBit down", source="arr-stack", thread_id="inc-1", collapse_id="inc-1")
        send.assert_called_once_with("Arr", "qBit down", thread_id="inc-1", collapse_id="inc-1")

    def test_info_alerts_do_not_buzz_the_phone(self) -> None:
        with patch("brain.notifications.configured", return_value=True), patch("brain.notifications.send") as send:
            event_bus.publish("device_online", "TV back", "ok", source="home", severity="info", push=False)
        send.assert_not_called()

    def test_failed_delivery_lands_in_inbox_without_republishing(self) -> None:
        with patch("brain.notifications.configured", return_value=True), \
             patch("brain.notifications.send", return_value=Delivery(False, None, "no route")):
            self.runtime.raise_alert("download_error", "Download error", "Dune", source="downloads")
        titles = [c.kwargs["title"] for c in self.runtime.record_event.call_args_list]
        self.assertEqual(titles, ["Download error", "Phone alert delivery failed"])
        self.assertEqual(len(event_bus.since(0)), 1)


class ClientTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(client_tokens, "_FILE", Path(tmp.name) / "client_tokens.json")
        p.start()
        self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin"})
        env.start()
        self.addCleanup(env.stop)
        event_bus.reset_for_tests()
        server._runtime = Mock()
        self.addCleanup(setattr, server, "_runtime", None)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

    def test_hash_only_storage_and_revoke(self) -> None:
        token = client_tokens.create("tv", ["events:read"])
        self.assertNotIn(token, client_tokens._FILE.read_text())
        self.assertEqual(client_tokens.scopes_for(token), ("tv", frozenset({"events:read"})))
        client_tokens.revoke("tv")
        self.assertIsNone(client_tokens.scopes_for(token))

    def test_unknown_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            client_tokens.create("tv", ["chat"])

    def test_tv_token_is_limited_to_its_scopes(self) -> None:
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        with patch.object(server.status_hub, "now_status", return_value={"ok": True}):
            self.assertEqual(self.client.get("/api/now", headers=tv).status_code, 200)
        self.assertEqual(self.client.get("/api/clients/events", headers=tv).status_code, 403)
        self.assertEqual(self.client.get("/sessions", headers=tv).status_code, 401)
        self.assertEqual(self.client.post("/chat", headers=tv, json={"message": "hi"}).status_code, 401)

    def test_recent_alerts_newest_first_and_scoped(self) -> None:
        for title in ("old", "new"):
            event_bus.publish("x", title, "m", source="s")
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        body = self.client.get("/api/alerts/recent", headers=tv).json()
        self.assertEqual([a["title"] for a in body["alerts"]], ["new", "old"])
        events_only = {"Authorization": "Bearer " + client_tokens.create("tv2", ["events:read"])}
        self.assertEqual(self.client.get("/api/alerts/recent", headers=events_only).status_code, 403)

    def test_event_stream_replays_then_streams_live(self) -> None:
        # Drive the endpoint's generator directly with timeouts: TestClient
        # can block forever on a never-ending stream (seen on Python 3.11).
        event_bus.publish("x", "old", "seen already", source="s")
        event_bus.publish("x", "missed", "replay me", source="s")

        async def scenario() -> list[str]:
            request = Mock(is_disconnected=AsyncMock(return_value=False))
            response = await server.client_events(request, since=0, last_event_id="1", _=None)
            self.assertEqual(response.media_type, "text/event-stream")
            frames = response.body_iterator
            try:
                first = await asyncio.wait_for(anext(frames), 5)
                # Publish from another thread, as monitors do.
                threading.Timer(0.1, lambda: event_bus.publish("y", "live", "now", source="s")).start()
                second = await asyncio.wait_for(anext(frames), 5)
            finally:
                await frames.aclose()
            return [json.loads(f.split("data: ", 1)[1])["title"] for f in (first, second)]

        self.assertEqual(asyncio.run(scenario()), ["missed", "live"])
        self.assertEqual([n for n in event_bus._subscribers if n.startswith("sse-")], [])

if __name__ == "__main__":
    unittest.main()
