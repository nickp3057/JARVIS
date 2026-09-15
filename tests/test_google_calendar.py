"""Google Calendar: multi-calendar merge and degradation."""

import os
import unittest
from unittest.mock import patch

from integrations import google_calendar as gc


class _Exec:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def execute(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.payload


class _Events:
    def __init__(self, by_calendar, failing=()):
        self.by_calendar, self.failing = by_calendar, set(failing)
        self.calls = []

    def list(self, calendarId, **params):
        self.calls.append((calendarId, params))
        if calendarId in self.failing:
            return _Exec(error=f"{calendarId} down")
        return _Exec({"items": self.by_calendar.get(calendarId, [])})


class _Service:
    def __init__(self, calendars, by_calendar, failing=(), list_error=None):
        self._calendars, self._list_error = calendars, list_error
        self._events = _Events(by_calendar, failing)

    def calendarList(self):
        svc = self

        class _CL:
            def list(self, **_):
                if svc._list_error:
                    return _Exec(error=svc._list_error)
                return _Exec({"items": svc._calendars})

        return _CL()

    def events(self):
        return self._events


def _ev(eid, title, when):
    return {"id": eid, "summary": title, "start": {"dateTime": when}}


CALENDARS = [
    {"id": "primary", "summary": "Nick"},
    {"id": "school@group.calendar.google.com", "summary": "School"},
    {"id": "hidden@group", "summary": "Old", "hidden": True},
]
EVENTS = {
    "primary": [_ev("w1", "Hotschedules shift", "2026-09-16T16:00:00-06:00")],
    "school@group.calendar.google.com": [_ev("q1", "CS 3550 Quiz 2", "2026-09-16T10:00:00-06:00")],
}


class MultiCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        gc._calendar_list_cache = None
        self.env = patch.dict(os.environ, {"GOOGLE_CALENDAR_ID": ""})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        gc._calendar_list_cache = None

    def test_all_calendars_are_merged_in_start_order_and_tagged(self) -> None:
        svc = _Service(CALENDARS, EVENTS)
        with patch.object(gc, "_get_service", return_value=svc):
            events = gc.upcoming_events(max_results=10, hours_ahead=48)
        self.assertEqual([e["summary"] for e in events], ["CS 3550 Quiz 2", "Hotschedules shift"])
        self.assertEqual([e["calendar"] for e in events], ["School", "Nick"])
        queried = {c for c, _ in svc.events().calls}
        self.assertEqual(queried, {"primary", "school@group.calendar.google.com"})

    def test_explicit_id_list_skips_calendar_list_lookup(self) -> None:
        svc = _Service(CALENDARS, EVENTS, list_error="should not be called")
        with patch.dict(os.environ, {"GOOGLE_CALENDAR_ID": "primary"}), \
             patch.object(gc, "_get_service", return_value=svc):
            events = gc.upcoming_events(max_results=10)
        self.assertEqual([e["summary"] for e in events], ["Hotschedules shift"])

    def test_one_failing_calendar_does_not_hide_the_others(self) -> None:
        svc = _Service(CALENDARS, EVENTS, failing={"primary"})
        with patch.object(gc, "_get_service", return_value=svc):
            events = gc.upcoming_events(max_results=10)
        self.assertEqual([e["summary"] for e in events], ["CS 3550 Quiz 2"])

    def test_all_calendars_failing_returns_none(self) -> None:
        svc = _Service(CALENDARS, EVENTS, failing={"primary", "school@group.calendar.google.com"})
        with patch.object(gc, "_get_service", return_value=svc):
            self.assertIsNone(gc.upcoming_events(max_results=10))

    def test_calendar_list_failure_falls_back_to_primary(self) -> None:
        svc = _Service(CALENDARS, EVENTS, list_error="quota")
        with patch.object(gc, "_get_service", return_value=svc):
            events = gc.upcoming_events(max_results=10)
        self.assertEqual([e["summary"] for e in events], ["Hotschedules shift"])

    def test_calendar_list_is_cached(self) -> None:
        svc = _Service(CALENDARS, EVENTS)
        with patch.object(gc, "_get_service", return_value=svc):
            gc.upcoming_events(max_results=10)
            svc._list_error = "second lookup should hit the cache"
            events = gc.upcoming_events(max_results=10)
        self.assertEqual(len(events), 2)

    def test_same_event_on_two_calendars_is_reported_once(self) -> None:
        events = {
            "primary": [_ev("a", "CS 3190 Quiz 2", "2026-09-24T10:00:00-06:00"),
                        _ev("b", "Different thing", "2026-09-24T10:00:00-06:00")],
            "school@group.calendar.google.com": [_ev("c", "cs 3190  quiz 2", "2026-09-24T10:00:00-06:00")],
        }
        svc = _Service(CALENDARS, events)
        with patch.object(gc, "_get_service", return_value=svc):
            merged = gc.upcoming_events(max_results=10)
        self.assertEqual(sorted(e["summary"] for e in merged), ["CS 3190 Quiz 2", "Different thing"])
        self.assertEqual(next(e for e in merged if "Quiz" in e["summary"])["calendar"], "Nick")

    def test_partial_fetch_is_flagged(self) -> None:
        svc = _Service(CALENDARS, EVENTS, failing={"primary"})
        with patch.object(gc, "_get_service", return_value=svc):
            gc.upcoming_events(max_results=10)
            self.assertTrue(gc.last_fetch_partial)
            svc._events.failing = set()
            gc.upcoming_events(max_results=10)
            self.assertFalse(gc.last_fetch_partial)

    def test_api_calls_are_serialised(self) -> None:
        """Two threads fetching at once never overlap inside the client."""
        import threading

        inside = 0
        overlap = False
        gate = threading.Lock()

        class _SlowExec(_Exec):
            def execute(self):
                nonlocal inside, overlap
                with gate:
                    inside += 1
                    if inside > 1:
                        overlap = True
                import time
                time.sleep(0.01)
                with gate:
                    inside -= 1
                return super().execute()

        class _SlowEvents(_Events):
            def list(self, calendarId, **params):
                return _SlowExec({"items": self.by_calendar.get(calendarId, [])})

        svc = _Service(CALENDARS, EVENTS)
        svc._events = _SlowEvents(EVENTS)
        with patch.dict(os.environ, {"GOOGLE_CALENDAR_ID": "primary,school@group.calendar.google.com"}), \
             patch.object(gc, "_get_service", return_value=svc):
            threads = [threading.Thread(target=gc.upcoming_events, kwargs={"max_results": 10}) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertFalse(overlap)

    def test_max_results_applies_after_merge(self) -> None:
        svc = _Service(CALENDARS, EVENTS)
        with patch.object(gc, "_get_service", return_value=svc):
            events = gc.upcoming_events(max_results=1)
        self.assertEqual([e["summary"] for e in events], ["CS 3550 Quiz 2"])


if __name__ == "__main__":
    unittest.main()
