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
from brain import event_bus, theme, wake_alarm
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

    def test_slow_phone_does_not_block_streams_and_preserves_fifo(self) -> None:
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        delivered = []
        def phone(alert):
            entered.set()
            release.wait(3)
            delivered.append(alert.title)
            if len(delivered) == 2:
                finished.set()
        event_bus.subscribe("phone", phone, background=True)
        self.addCleanup(event_bus.reset_for_tests)
        stream = Mock()
        event_bus.subscribe("stream", stream)
        try:
            first = event_bus.publish("x", "first", "", source="test")
            self.assertTrue(entered.wait(1))
            second = event_bus.publish("x", "second", "", source="test")
            self.assertEqual([c.args[0] for c in stream.call_args_list], [first, second])
            self.assertEqual(delivered, [])
        finally:
            release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(delivered, ["first", "second"])

    def test_dedupe_within_cooldown(self) -> None:
        first = event_bus.publish("x", "t", "m", source="s", dedupe_key="k", cooldown_s=60)
        second = event_bus.publish("x", "t", "m", source="s", dedupe_key="k", cooldown_s=60)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_same_warning_again_is_dropped_even_after_a_restart(self) -> None:
        first = event_bus.publish("schwab_login", "Schwab needs you", "Log in.", source="s")
        self.assertIsNotNone(first)
        self.assertEqual(first.grouping["collapse_id"], "a-" + event_bus.condense_key(
            "schwab_login", "Schwab needs you", "Log in."))
        self.assertEqual(first.grouping["thread_id"], "schwab_login")
        event_bus._repeats = None  # a restart: reload from the file
        self.assertFalse(event_bus.publish("schwab_login", "Schwab needs you", "Log in.", source="s").push)
        self.assertIsNotNone(event_bus.publish("schwab_login", "Schwab needs you", "Other.", source="s"))
        self.assertIsNotNone(event_bus.publish("x", "Back online", "m", source="s", severity="info"))
        self.assertIsNotNone(event_bus.publish("x", "Back online", "m", source="s", severity="info"))
        with patch.dict(os.environ, {"ALERT_REPEAT_WINDOW_S": "0"}):
            self.assertIsNotNone(event_bus.publish("schwab_login", "Schwab needs you", "Log in.", source="s"))

    def test_repeat_survives_restart_in_history_without_duplicate_push(self) -> None:
        stream = Mock()
        event_bus.subscribe("stream", stream)
        first = event_bus.publish("device_offline", "Lamp offline", "Down", source="home")
        self.assertTrue(first.push)
        repeated = event_bus.publish("device_offline", "Lamp offline", "Down", source="home")
        self.assertFalse(repeated.push)
        self.assertEqual(stream.call_count, 1)  # no repeat TV banner
        event_bus._history.clear()  # process restart: history is in memory
        event_bus._repeats = None  # repeat suppression survives on disk
        again = event_bus.publish("device_offline", "Lamp offline", "Down", source="home")
        self.assertFalse(again.push)
        self.assertEqual(event_bus.since(), [again])
        self.assertEqual(stream.call_count, 2)  # first sight after restart is live

    def test_recurrence_after_resolution_and_escalation_push(self) -> None:
        first = event_bus.publish("nas_host", "Disk", "Bad", source="nas-host")
        event_bus.resolve([first.id])
        again = event_bus.publish("nas_host", "Disk", "Bad", source="nas-host")
        self.assertTrue(again.push)
        critical = event_bus.publish("nas_host", "Disk", "Bad", source="nas-host", severity="critical")
        self.assertTrue(critical.push)
        repeated = event_bus.publish("nas_host", "Disk", "Bad", source="nas-host", severity="critical")
        self.assertFalse(repeated.push)
        self.assertEqual(len(event_bus.since()), 4)

    def test_explicit_collapse_id_wins(self) -> None:
        alert = event_bus.publish("x", "t", "m", source="s", collapse_id="wake-up")
        self.assertEqual(alert.grouping["collapse_id"], "wake-up")

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
        send.assert_called_once()
        self.assertEqual(send.call_args.args, ("Arr", "qBit down"))
        kwargs = send.call_args.kwargs
        self.assertEqual((kwargs["thread_id"], kwargs["collapse_id"]), ("inc-1", "inc-1"))
        # A critical alert carries its id so tapping the push opens it in the app.
        self.assertEqual(kwargs["alert_id"], event_bus.since(0)[-1].id)

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
        for target, name in ((client_tokens, "client_tokens.json"), (theme, "theme.json"),
                             (wake_alarm, "wake_alarm.json")):
            p = patch.object(target, "_FILE", Path(tmp.name) / name)
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
            client_tokens.create("tv", ["admin"])

    def test_tv_token_is_limited_to_its_scopes(self) -> None:
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        with patch.object(server.status_hub, "now_status", return_value={"ok": True}):
            self.assertEqual(self.client.get("/api/now", headers=tv).status_code, 200)
        self.assertEqual(self.client.get("/api/clients/events", headers=tv).status_code, 403)
        self.assertEqual(self.client.get("/sessions", headers=tv).status_code, 401)
        self.assertEqual(self.client.post("/chat", headers=tv, json={"message": "hi"}).status_code, 403)

    def test_recent_alerts_newest_first_and_scoped(self) -> None:
        for title in ("old", "new"):
            event_bus.publish("x", title, "m", source="s")
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        body = self.client.get("/api/alerts/recent", headers=tv).json()
        self.assertEqual([a["title"] for a in body["alerts"]], ["new", "old"])
        events_only = {"Authorization": "Bearer " + client_tokens.create("tv2", ["events:read"])}
        self.assertEqual(self.client.get("/api/alerts/recent", headers=events_only).status_code, 403)

    def test_clearing_empties_needs_attention_but_new_alerts_show(self) -> None:
        event_bus.publish("x", "Schwab needs you", "m", source="s")
        event_bus.publish("x", "Back online", "m", source="s", severity="info")
        admin = {"Authorization": "Bearer admin"}
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        self.assertEqual(self.client.post("/api/alerts/clear", headers=tv).status_code, 401)  # admin only
        self.assertEqual(self.client.post("/api/alerts/clear", headers=admin).json(), {"cleared": 1})
        self.assertEqual(self.client.get("/api/alerts/recent", headers=admin).json()["alerts"], [])
        event_bus.publish("x", "Disk filling", "m", source="s")
        titles = [a["title"] for a in self.client.get("/api/alerts/recent", headers=admin).json()["alerts"]]
        self.assertEqual(titles, ["Disk filling"])
        from brain.tools import execute_tool
        self.assertIn("Cleared 1 alert", execute_tool("clear_alerts", {}))
        self.assertIn("already empty", execute_tool("clear_alerts", {}))

    def test_recent_alerts_condense_the_same_problem(self) -> None:
        with patch.dict(os.environ, {"ALERT_REPEAT_WINDOW_S": "0"}):
            for _ in range(3):
                event_bus.publish("stall", "Download stalled", "Deadpool", source="s")
            event_bus.publish("stall", "Download stalled", "Dune", source="s")
        body = self.client.get("/api/alerts/recent", headers={"Authorization": "Bearer admin"}).json()
        self.assertEqual([(a["message"], a["repeats"]) for a in body["alerts"]], [("Dune", 1), ("Deadpool", 3)])
        self.assertEqual(body["alerts"][1]["id"], 3)  # the newest of the three

    def test_glance_for_tv_degrades_per_section(self) -> None:
        event_bus.publish("x", "Disk filling", "m", source="s")
        torrents = {"torrents": [
            {"name": "Dune", "progress": 0.4, "eta": 60, "state": "downloading"},
            {"name": "Paused", "progress": 0.1, "eta": -1, "state": "pausedDL"},
            {"name": "Done", "progress": 1.0, "eta": 0, "state": "uploading"},
        ]}
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        with patch.dict(os.environ, {"JARVIS_THEME": "hotRod", "ADDRESS": "sir"}), \
             patch.object(server.status_hub, "now_status", return_value={"reminders": []}), \
             patch.object(server.status_hub, "weather_status", side_effect=RuntimeError("offline")), \
             patch.object(server.status_hub, "torrents_status", return_value=torrents):
            body = self.client.get("/api/glance", headers=tv).json()
        self.assertEqual((body["theme"], body["address"]), ("hotRod", "sir"))
        self.assertEqual(body["weather"], {"error": "offline"})
        self.assertEqual([d["name"] for d in body["downloads"]], ["Dune"])
        self.assertEqual([a["title"] for a in body["alerts"]], ["Disk filling"])
        events_only = {"Authorization": "Bearer " + client_tokens.create("tv2", ["events:read"])}
        self.assertEqual(self.client.get("/api/glance", headers=events_only).status_code, 403)

    def test_theme_is_shared_and_needs_settings_scope(self) -> None:
        reader = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        writer = {"Authorization": "Bearer " + client_tokens.create("tv2", ["status:read", "settings:write"])}
        with patch.dict(os.environ, {"JARVIS_THEME": "markIII"}):
            first = self.client.get("/api/theme", headers=reader).json()
            self.assertEqual((first["theme"], first["chosen"]), ("hotRod", False))  # Mark III retired
            self.assertEqual(self.client.put("/api/theme", headers=reader, json={"theme": "arc"}).status_code, 403)
            self.assertEqual(self.client.put("/api/theme", headers=writer, json={"theme": "pink"}).status_code, 422)
            body = self.client.put("/api/theme", headers=writer, json={"theme": "hotRod"}).json()
            self.assertEqual((body["theme"], body["chosen"]), ("hotRod", True))
            self.assertEqual(self.client.get("/api/theme", headers=reader).json()["theme"], "hotRod")
        admin = {"Authorization": "Bearer admin"}
        self.assertEqual(self.client.put("/api/theme", headers=admin, json={"theme": "arc"}).json()["theme"], "arc")

    def test_every_theme_in_brain_theme_is_accepted(self) -> None:
        # Regression: the API once kept its own regex and refused "experimenting".
        from brain import theme
        admin = {"Authorization": "Bearer admin"}
        for name in theme.THEMES:
            self.assertEqual(self.client.put("/api/theme", headers=admin, json={"theme": name}).json()["theme"], name)

    def test_experiment_switches_are_shared_and_survive_theme_changes(self) -> None:
        admin = {"Authorization": "Bearer admin"}
        put = lambda body: self.client.put("/api/theme", headers=admin, json=body)  # noqa: E731
        self.assertEqual(put({"theme": "experimenting"}).json()["experiments"], {})
        self.assertEqual(put({"experiments": {"plasma": False}}).json()["experiments"], {"plasma": False})
        body = put({"experiments": {"neural": True}}).json()
        self.assertEqual((body["theme"], body["experiments"]), ("experimenting", {"plasma": False, "neural": True}))
        self.assertEqual(put({"theme": "spiderMan"}).json()["experiments"], {"plasma": False, "neural": True})
        self.assertEqual(put({"experiments": {"bad name!": True}}).status_code, 422)
        self.assertEqual(put({}).status_code, 422)

    def test_core_is_neural_under_experimenting_and_through_barn_door(self) -> None:
        from brain import theme
        theme.choose("experimenting")
        self.assertEqual(theme.current()["core"], "neural")
        theme.choose("barnDoor", remember=True)
        self.assertEqual(theme.current()["core"], "neural")  # spiky, not the old red reactor
        theme.set_experiments({"neural": False})
        self.assertEqual(theme.current()["core"], "classic")
        theme.restore()
        theme.set_experiments({"neural": True})
        self.assertEqual(theme.current()["core"], "neural")
        self.assertEqual(theme.current()["lab"], {"plasma": True, "neural": True, "orb3D": False, "glass": True})
        theme.set_experiments({"glass": False})
        self.assertFalse(theme.current()["lab"]["glass"])
        theme.choose("arc")
        self.assertEqual((theme.current()["core"], theme.current()["lab"]), ("classic", {}))

    def test_experiment_switch_moves_the_revision_the_home_stream_carries(self) -> None:
        from brain import theme
        admin = {"Authorization": "Bearer admin"}
        before = self.client.put("/api/theme", headers=admin, json={"theme": "experimenting"}).json()["revision"]
        with patch("brain.theme.time.time", return_value=before + 5):
            after = self.client.put("/api/theme", headers=admin, json={"experiments": {"neural": False}}).json()
        self.assertEqual(after["revision"], before + 5)  # theme unchanged, but screens still refetch
        self.assertEqual(theme.current()["revision"], before + 5)

    def test_wake_schedule_editor_is_admin_only(self) -> None:
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read", "settings:write"])}
        admin = {"Authorization": "Bearer admin"}
        self.assertEqual(self.client.get("/api/wake", headers=tv).status_code, 401)
        self.assertEqual(self.client.get("/api/wake", headers=admin).json()["times"], [None] * 7)
        times = ["6:30", "8:00", "6:30", None, "6:30", "9:00", None]
        body = self.client.put("/api/wake", headers=admin, json={
            "times": times, "music": "Back in Black", "volume": 25}).json()
        self.assertEqual(body["times"], ["06:30", "08:00", "06:30", None, "06:30", "09:00", None])
        self.assertEqual((body["label"], body["volume"], body["enabled"]), ("Back in Black", 25, True))
        self.assertIn("6:30 AM Mon, Wed, Fri", body["summary"])
        bad = self.client.put("/api/wake", headers=admin, json={"times": ["25:00"] + [None] * 6})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.put("/api/wake", headers=admin, json={"times": [None]}).status_code, 422)

    def test_chat_scope_lets_a_client_talk(self) -> None:
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["chat"])}
        server._runtime.start_session.return_value = "s1"
        server._runtime.respond_in_session.return_value = "At once, sir."
        with patch.object(server, "session_exists", return_value=True):
            reply = self.client.post("/chat", headers=tv, json={"message": "lights off"})
        self.assertEqual(reply.json(), {"reply": "At once, sir.", "session_id": "s1"})
        self.assertEqual(self.client.get("/sessions", headers=tv).status_code, 401)

    def test_glance_events_are_categorised_and_work_hidden(self) -> None:
        from datetime import datetime, timedelta, timezone
        soon = datetime.now(timezone.utc) + timedelta(hours=3)
        events = [
            {"summary": "Fryer filter", "start": soon, "all_day": True, "calendar": "Oil/Boil Outs"},
            {"summary": "Quiz 4", "start": soon, "all_day": False, "calendar": "School"},
            {"summary": "Dinner", "start": soon, "all_day": False, "calendar": "Pearson Family Calendar"},
            {"summary": "Gym", "start": soon, "all_day": False, "calendar": "nick@example.com"},
            {"summary": "Far off", "start": soon + timedelta(days=30), "all_day": True, "calendar": "School"},
        ]
        with patch.dict(os.environ, {"CALENDAR_CATEGORIES": "Oil/Boil Outs=work"}), \
             patch.object(server.status_hub, "calendar_configured", return_value=True), \
             patch.object(server.status_hub, "_calendar_events", return_value=events):
            body = server.status_hub._glance_events()
        self.assertEqual([(e["title"], e["category"]) for e in body],
                         [("Quiz 4", "school"), ("Dinner", "family"), ("Gym", "personal")])

    def test_event_stream_replays_then_streams_live(self) -> None:
        # Drive the endpoint's generator directly with timeouts: TestClient
        # can block forever on a never-ending stream (seen on Python 3.11).
        event_bus.publish("x", "old", "seen already", source="s")
        event_bus.publish("x", "missed", "replay me", source="s")

        async def scenario() -> list[str]:
            request = Mock(is_disconnected=AsyncMock(return_value=False))
            response = await server.client_events(request, since=0, last_event_id="1", client=None)
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
