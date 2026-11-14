"""'Let me know when X happens': watches, the watcher subagent, and delivery.

The model and the web are always faked (FakeClient); nothing reaches the API."""
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from brain import event_bus, memory_extractor, subagents, tools, usage, voice_trust, watches
from brain.reminders import Reminder
from brain.timers import Timer
from tests.test_runtime_core import make_runtime


class _Resp:
    def __init__(self, text):
        self.content = [{"type": "text", "text": text}]
        self.model = "claude-test"
        self.usage = None


class FakeClient:
    """Answers every request with the next scripted verdict text."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return _Resp(self.texts.pop(0))


def _verdict(met, answer="", note=""):
    return json.dumps({"met": met, "answer": answer, "note": note})


ENV = {"WATCH_MODEL": "claude-test", "WATCH_MAX_CHECKS_PER_DAY": "12", "WATCH_MAX_CHECKS_PER_WATCH": "4",
       "WATCH_DEFAULT_CADENCE_HOURS": "6", "WATCH_DEFAULT_EXPIRY_DAYS": "14", "WATCH_MIN_INTERVAL_MIN": "30",
       "WATCH_MONITOR_ENABLED": "true", "WATCH_ANNOUNCE": "true"}


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(watches, "_FILE", Path(self.tmp.name) / "watches.json"),
                  patch.dict("os.environ", ENV)):
            p.start()
            self.addCleanup(p.stop)
        subagents.reset_for_tests()
        self.addCleanup(subagents.reset_for_tests)


class CadenceTests(_StoreCase):
    def test_words_become_intervals(self):
        self.assertEqual(watches.parse_cadence(""), (6 * 3600, ""))
        self.assertEqual(watches.parse_cadence("hourly"), (3600, ""))
        self.assertEqual(watches.parse_cadence("check every 3 hours"), (3 * 3600, ""))
        self.assertEqual(watches.parse_cadence("every two hours"), (2 * 3600, ""))
        self.assertEqual(watches.parse_cadence("twice a day"), (43_200, ""))
        self.assertEqual(watches.parse_cadence("daily"), (86_400, ""))
        self.assertEqual(watches.parse_cadence("check every morning"), (86_400, "08:00"))
        self.assertEqual(watches.parse_cadence("every day at 9:30 pm"), (86_400, "21:30"))
        self.assertEqual(watches.parse_cadence("every 5 minutes"), (1800, ""))  # the floor
        self.assertEqual(watches.parse_cadence("whenever you like"), (6 * 3600, ""))

    def test_every_morning_checks_now_then_at_eight(self):
        now = datetime(2026, 11, 14, 15, 0).timestamp()
        item = watches.add_watch("q", "c", cadence="every morning", now=now)
        self.assertEqual(watches.next_due(item), now)  # first look right away
        item.last_checked = now
        self.assertEqual(datetime.fromtimestamp(watches.next_due(item)), datetime(2026, 11, 15, 8, 0))

    def test_not_before_holds_the_first_check(self):
        now = time.time()
        drop = datetime.fromtimestamp(now) + timedelta(days=2)
        item = watches.add_watch("AP poll", "it's out", not_before=drop.isoformat(timespec="minutes"), now=now)
        self.assertGreater(watches.next_due(item), now + 86_400)
        self.assertGreater(item.expires_at, item.not_before)
        with self.assertRaises(ValueError):
            watches.add_watch("q", "c", not_before="next sunday-ish")


class StoreTests(_StoreCase):
    def test_add_list_cancel(self):
        a = watches.add_watch("AP Top 25 poll week 12", "the poll is out; BYU's rank", label="AP poll")
        watches.add_watch("PS5 Pro restock", "in stock at a major US retailer", label="PS5 restock")
        self.assertIn("AP poll", watches.describe_watches())
        self.assertIsNone(watches.cancel_watch("nonexistent"))
        self.assertEqual(watches.cancel_watch("ps5").label, "PS5 restock")  # by a word
        self.assertEqual(watches.cancel_watch(a.id).id, a.id)  # by id
        self.assertEqual(watches.active_watches(), [])
        self.assertIn("Finished (cancelled)", watches.describe_watches())

    def test_needs_query_and_condition(self):
        with self.assertRaises(ValueError):
            watches.add_watch("", "x")
        with self.assertRaises(ValueError):
            watches.add_watch("x", " ")


class CheckTests(_StoreCase):
    def test_met_retires_with_the_answer_and_records_usage(self):
        item = watches.add_watch("AP poll week 12", "the poll is out; BYU's rank", label="AP poll")
        client = FakeClient("Searching...\n" + _verdict(True, "AP poll's out, BYU is #12."))
        with patch.object(usage, "record_response") as recorded:
            outcomes = watches.check_due(client=client)
        self.assertEqual([(o["event"], o["answer"]) for o in outcomes], [("met", "AP poll's out, BYU is #12.")])
        recorded.assert_called_once()
        self.assertEqual(recorded.call_args.args[0], "watcher")
        request = client.requests[0]
        self.assertEqual(request["model"], "claude-test")
        search = [t for t in request["tools"] if t["name"] == "web_search"]
        self.assertEqual(len(search), 1)
        self.assertTrue(search[0]["type"].startswith("web_search_"))
        self.assertIn("AP poll week 12", request["messages"][0]["content"])
        self.assertEqual(watches.active_watches(), [])
        done = next(w for w in watches.all_watches() if w.id == item.id)
        self.assertEqual((done.status, done.checks), ("done", 1))

    def test_not_met_waits_for_the_cadence(self):
        now = time.time()
        watches.add_watch("Michael 2 release date", "a release date is announced", now=now)
        outcomes = watches.check_due(now=now, client=FakeClient(_verdict(False, note="No date yet.")))
        self.assertEqual(outcomes, [])
        (w,) = watches.active_watches()
        self.assertEqual((w.last_note, w.checks, w.checks_today), ("No date yet.", 1, 1))
        self.assertEqual(watches.due_count(now + 60), 0)
        self.assertEqual(watches.check_due(now=now + 60, client=FakeClient()), [])  # not due: no call
        self.assertEqual(watches.due_count(now + 6 * 3600 + 1), 1)

    def test_garbage_reply_is_not_met(self):
        watches.add_watch("q", "c")
        self.assertEqual(watches.check_due(client=FakeClient("I think so, probably!")), [])
        self.assertEqual(len(watches.active_watches()), 1)

    def test_daily_and_per_watch_caps(self):
        now = datetime(2026, 11, 14, 9, 0).timestamp()
        for i in range(3):
            watches.add_watch(f"thing {i}", "it happened", cadence="every 30 minutes", now=now)
        with patch.dict("os.environ", {"WATCH_MAX_CHECKS_PER_DAY": "2"}):
            client = FakeClient(*[_verdict(False, note="nope")] * 5)
            watches.check_due(now=now, client=client)
            self.assertEqual(len(client.requests), 2)  # the day's budget
            self.assertEqual(watches.due_count(now + 3600), 0)  # capped: no empty jobs
        with patch.dict("os.environ", {"WATCH_MAX_CHECKS_PER_DAY": "50", "WATCH_MAX_CHECKS_PER_WATCH": "1"}):
            client = FakeClient(*[_verdict(False, note="nope")] * 5)
            watches.check_due(now=now + 3600, client=client)
            self.assertEqual(len(client.requests), 1)  # only the unchecked one is left today
            tomorrow = now + 86_400
            client = FakeClient(*[_verdict(False, note="nope")] * 5)
            watches.check_due(now=tomorrow, client=client)
            self.assertEqual(len(client.requests), 3)  # a new day resets the counts

    def test_expiry_retires_quietly(self):
        now = time.time()
        watches.add_watch("q", "c", expires_in_days=1, now=now)
        outcomes = watches.check_due(now=now + 2 * 86_400, client=FakeClient())
        self.assertEqual([o["event"] for o in outcomes], ["expired"])
        self.assertEqual(watches.active_watches(), [])

    def test_no_api_key_costs_nothing(self):
        watches.add_watch("q", "c")
        with patch.object(subagents, "_client", return_value=None):
            self.assertEqual(watches.check_due(), [])
        (w,) = watches.active_watches()
        self.assertEqual(w.checks, 0)
        self.assertIn("Check failed", w.last_note)


class RuntimeDeliveryTests(_StoreCase):
    def setUp(self):
        super().setUp()
        event_bus.reset_for_tests()
        self.addCleanup(event_bus.reset_for_tests)
        self.rt = make_runtime()
        self.addCleanup(memory_extractor.remove_listener, self.rt._on_memory_change)

    def test_found_watch_alerts_phone_and_room_once(self):
        watches.add_watch("AP poll", "it's out", label="AP poll")
        summary = self.rt.run_watch_checks(client=FakeClient(_verdict(True, "AP poll's out, BYU is #12.")))
        self.assertIn("AP poll", summary)
        (alert,) = event_bus.since(0)
        self.assertEqual((alert.kind, alert.severity, alert.push), ("watch", "info", True))
        self.assertEqual(alert.message, "AP poll's out, BYU is #12.")
        inbox = [e for e in self.rt.recent_events() if e.source == "watcher"]
        self.assertEqual(len(inbox), 1)  # one inbox line, not one per channel
        with patch("brain.runtime.pop_expired", return_value=[]), \
                patch("brain.runtime._pop_due_reminders", return_value=[]):
            spoken = self.rt.pop_due_reminders()
        self.assertEqual([s.message for s in spoken], ["AP poll's out, BYU is #12."])
        self.assertEqual(len([e for e in self.rt.recent_events() if e.source == "watcher"]), 1)

    def test_nothing_due_runs_no_job(self):
        self.assertIsNone(self.rt.run_watch_checks(client=FakeClient()))
        self.assertEqual(subagents.jobs_snapshot(), [])

    def test_expired_watch_leaves_a_quiet_note(self):
        watches.add_watch("q", "c", label="Restock", expires_in_days=1, now=time.time() - 2 * 86_400)
        self.rt.run_watch_checks(client=FakeClient())
        self.assertEqual(event_bus.since(0), [])  # no push, no TV banner
        note = next(e for e in self.rt.recent_events() if e.source == "watcher")
        self.assertEqual((note.kind, note.title), ("notice", "Watch expired"))
        self.assertIn("Restock", note.detail)


class ReminderDeliveryTests(unittest.TestCase):
    def setUp(self):
        event_bus.reset_for_tests()
        self.addCleanup(event_bus.reset_for_tests)
        self.rt = make_runtime()
        self.addCleanup(memory_extractor.remove_listener, self.rt._on_memory_change)

    def _pop(self, timers=(), reminders=()):
        with patch("brain.runtime.pop_expired", return_value=list(timers)), \
                patch("brain.runtime._pop_due_reminders", return_value=list(reminders)):
            return self.rt.pop_due_reminders()

    def test_due_reminder_reaches_the_bus_once(self):
        reminder = Reminder(id="r1", text="Take the bins out", due_at=0)
        due = self._pop(reminders=[reminder])
        self.assertEqual([d.message for d in due], ["Take the bins out"])  # still spoken by the room
        (alert,) = event_bus.since(0)
        self.assertEqual((alert.kind, alert.severity, alert.push), ("reminder", "info", True))
        self.assertEqual(alert.message, "Take the bins out")
        events = [e for e in self.rt.recent_events() if e.source == "reminders"]
        self.assertEqual([(e.kind, e.speakable) for e in events], [("reminder", True)])
        self.rt._record_reminder_event(reminder)  # seen again (GUI observe): no second push
        self.assertEqual(len(event_bus.since(0)), 1)

    def test_timers_and_announcements_stay_off_the_phone(self):
        timer = Timer(id="t7", expires_at=0, message="Your pasta timer is done")
        self._pop(timers=[timer])
        self.rt.announce("The laundry is done, sir.")
        self._pop()
        self.assertEqual(event_bus.since(0), [])
        titles = [e.title for e in self.rt.recent_events()]
        self.assertEqual(titles.count("Reminder due"), 1)  # the timer; the announcement isn't logged twice


class ToolTests(_StoreCase):
    def test_watch_for_list_and_cancel_tools(self):
        reply = tools.execute_tool("watch_for", {"query": "AP Top 25 week 12", "done_when": "the poll is out",
                                                 "label": "AP poll", "cadence": "hourly"})
        self.assertIn("Watching (w", reply)
        self.assertIn("hourly", reply)
        self.assertIn("AP poll", tools.execute_tool("list_watches", {}))
        self.assertIn("Tool error", tools.execute_tool("watch_for", {"query": "x", "done_when": ""}))
        self.assertIn("Stopped watching", tools.execute_tool("cancel_watch", {"watch": "AP"}))

    def test_cancel_is_locked_to_nick(self):
        self.assertIn("cancel_watch", voice_trust.locked_tools())
        self.assertNotIn("watch_for", voice_trust.locked_tools())


if __name__ == "__main__":
    unittest.main()
