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

    def test_max_results_applies_after_merge(self) -> None:
        svc = _Service(CALENDARS, EVENTS)
        with patch.object(gc, "_get_service", return_value=svc):
            events = gc.upcoming_events(max_results=1)
        self.assertEqual([e["summary"] for e in events], ["CS 3550 Quiz 2"])


if __name__ == "__main__":
    unittest.main()
