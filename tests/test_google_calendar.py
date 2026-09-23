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


class HeadlessAuthTests(unittest.TestCase):
    """Boot-race refresh failures and headless hosts must not kill the calendar."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        gc._service, gc._service_failed, gc._consent_logged_at = None, False, 0.0
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = Path(self.tmp.name) / "token.json"
        self.token.write_text("{}")
        self.creds_file = Path(self.tmp.name) / "client.json"
        self.creds_file.write_text("{}")
        env = patch.dict(os.environ, {"GOOGLE_TOKEN_FILE": str(self.token), "GOOGLE_CREDENTIALS_FILE": str(self.creds_file)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(lambda: setattr(gc, "_service", None))
        self.addCleanup(lambda: setattr(gc, "_service_failed", False))

    def expired_creds(self, refresh_error):
        from unittest.mock import MagicMock
        creds = MagicMock(valid=False, expired=True, refresh_token="r")
        creds.refresh.side_effect = refresh_error
        return creds

    def test_network_error_on_refresh_is_retried_not_fatal(self):
        from google.auth.exceptions import TransportError
        creds = self.expired_creds(TransportError("Network is unreachable"))
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds), \
             patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file") as flow:
            self.assertIsNone(gc._get_service())
        flow.assert_not_called()
        self.assertFalse(gc._service_failed)
        # Next call, network up: refresh succeeds and the service is built.
        creds.refresh.side_effect = None
        creds.valid = True
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds), \
             patch("googleapiclient.discovery.build", return_value="svc"):
            self.assertEqual(gc._get_service(), "svc")

    def test_headless_host_never_opens_browser_and_stays_retryable(self):
        from google.auth.exceptions import RefreshError
        creds = self.expired_creds(RefreshError("invalid_grant"))
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds), \
             patch.object(gc, "_browser_available", return_value=False), \
             patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file") as flow, \
             self.assertLogs(gc.log, level="WARNING") as logs:
            self.assertIsNone(gc._get_service())
            self.assertIsNone(gc._get_service())
        flow.assert_not_called()
        self.assertFalse(gc._service_failed)
        self.assertEqual(sum("--auth" in line for line in logs.output), 1)

    def test_browser_detection_respects_headless_override_and_display(self):
        with patch.dict(os.environ, {"GOOGLE_OAUTH_HEADLESS": "true"}):
            self.assertFalse(gc._browser_available())
        with patch.object(gc.sys, "platform", "linux"), patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": "", "GOOGLE_OAUTH_HEADLESS": ""}):
            self.assertFalse(gc._browser_available())
