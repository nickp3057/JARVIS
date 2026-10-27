"""Daily study brief: settings, planning, composition, and the companion endpoint."""

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import app.status_hub as status_hub
from brain import study_brief
from integrations.google_calendar import academic_events_from


def _local(y: int, m: int, d: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute).astimezone()


NOW = _local(2026, 9, 15, 8, 0)
EVENTS = [
    {"id": "q1", "summary": "CS 3550 Quiz 2", "start": _local(2026, 9, 17, 10, 0), "all_day": False},
    {"id": "e1", "summary": "Midterm Exam", "start": _local(2026, 9, 30), "all_day": True},
    {"id": "w1", "summary": "Hotschedules: BOH shift", "start": _local(2026, 9, 16, 16, 0), "all_day": False},
    {"id": "old", "summary": "Quiz 1", "start": _local(2026, 9, 10, 10, 0), "all_day": False},
]

_DEFAULT_ENV = {
    "STUDY_BRIEF_ENABLED": "", "STUDY_BRIEF_HOUR": "", "STUDY_BRIEF_WINDOW_DAYS": "",
    "STUDY_BRIEF_KEYWORDS": "", "STUDY_BRIEF_STYLE": "", "ANTHROPIC_API_KEY": "",
}


class _FakeClient:
    def __init__(self, bodies=None, fail=False):
        self.bodies = bodies
        self.fail = fail
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model down")
        n = len(__import__("json").loads(kwargs["messages"][0]["content"]))
        bodies = self.bodies if self.bodies is not None else [f"Brief {i}, sir." for i in range(n)]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"bodies": bodies})])


class SettingsAndKeywordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, dict(_DEFAULT_ENV))
        self.env.start()
        study_brief.clear_cache()

    def tearDown(self) -> None:
        self.env.stop()

    def test_defaults(self) -> None:
        cfg = study_brief.settings()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["hour"], 9)
        self.assertEqual(cfg["window_days"], 14)
        self.assertEqual(cfg["keywords"], ["quiz", "test", "testlet", "exam", "midterm", "final"])
        self.assertEqual(cfg["assignment_window_days"], 7)
        self.assertIn("homework", cfg["assignment_keywords"])

    def test_classify_assessments_assignments_and_noise(self) -> None:
        self.assertEqual(study_brief.classify("CS 3550 Testlet 1"), "assessment")
        self.assertEqual(study_brief.classify("CS 3505 Testlets"), "assessment")
        self.assertEqual(study_brief.classify("Homework 4"), "assignment")
        self.assertEqual(study_brief.classify("Lab 2 due"), "assignment")
        self.assertEqual(study_brief.classify("Quiz 2 due"), "assessment")
        self.assertIsNone(study_brief.classify("Buy Parking Permit"))
        self.assertIsNone(study_brief.classify("Fall Break (No Classes)"))

    def test_assignments_use_their_own_shorter_window(self) -> None:
        events = [
            {"summary": "Homework 3", "start": _local(2026, 9, 20), "all_day": True},
            {"summary": "Homework 4", "start": _local(2026, 9, 27), "all_day": True},
            {"summary": "CS 3550 Testlet 1", "start": _local(2026, 9, 27, 9, 10), "all_day": False},
        ]
        briefs = study_brief.plan_briefs(events, now=NOW)
        first = briefs[0]["items"]
        self.assertEqual(first, [
            {"title": "Homework 3", "days_left": 5, "kind": "assignment"},
            {"title": "CS 3550 Testlet 1", "days_left": 12, "kind": "assessment"},
        ])
        self.assertEqual(study_brief.template_body(first).split("\n")[0], "Homework 3 due in 5 days.")
        with patch.dict(os.environ, {"STUDY_BRIEF_ASSIGNMENT_WINDOW_DAYS": "14"}):
            wide = study_brief.plan_briefs(events, now=NOW)[0]["items"]
        self.assertIn({"title": "Homework 4", "days_left": 12, "kind": "assignment"}, wide)

    def test_hub_countdown_tags_kind(self) -> None:
        events = [
            {"summary": "Homework 3", "start": _local(2026, 9, 20), "all_day": True},
            {"summary": "CS 3550 Testlet 1", "start": _local(2026, 9, 27, 9, 10), "all_day": False},
        ]
        kinds = [(e["summary"], e["kind"]) for e in academic_events_from(events, now=NOW)]
        self.assertEqual(kinds, [("Homework 3", "assignment"), ("CS 3550 Testlet 1", "assessment")])

    def test_env_overrides_and_clamping(self) -> None:
        with patch.dict(os.environ, {
            "STUDY_BRIEF_ENABLED": "false", "STUDY_BRIEF_HOUR": "31",
            "STUDY_BRIEF_WINDOW_DAYS": "0", "STUDY_BRIEF_KEYWORDS": " Lab , quiz,,LAB ",
            "STUDY_BRIEF_STYLE": "  short and   blunt ",
        }):
            cfg = study_brief.settings()
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["hour"], 23)
        self.assertEqual(cfg["window_days"], 1)
        self.assertEqual(cfg["keywords"], ["lab", "quiz"])
        self.assertEqual(cfg["style"], "short and blunt")

    def test_keyword_pattern_handles_plurals_and_word_boundaries(self) -> None:
        pattern = study_brief.keyword_pattern()
        for title in ("Quizzes due", "FINAL EXAM", "Midterms week", "Unit test"):
            self.assertIsNotNone(pattern.search(title), title)
        for title in ("Contest results", "Finalize plans", "Hotschedules shift"):
            self.assertIsNone(pattern.search(title), title)

    def test_calendar_academic_detection_follows_keywords_setting(self) -> None:
        events = [
            {"summary": "Lab 3", "start": _local(2026, 9, 20)},
            {"summary": "Quiz 2", "start": _local(2026, 9, 21)},
        ]
        with patch.dict(os.environ, {"STUDY_BRIEF_KEYWORDS": "lab"}):
            titles = [e["summary"] for e in academic_events_from(events, now=NOW)]
        self.assertEqual(titles, ["Lab 3"])


class SameDayDedupeTests(unittest.TestCase):
    PAIR = [
        {"id": "a", "summary": "CS 3190 Quiz 2 (Lec 5-8)", "start": _local(2026, 9, 24), "all_day": True},
        {"id": "b", "summary": "CS 3190  Quiz 2 (Lec 5-8)", "start": _local(2026, 9, 24, 10, 50), "all_day": False},
        {"id": "c", "summary": "CS 3190 Quiz 2 (Lec 5-8)", "start": _local(2026, 9, 25, 10, 50), "all_day": False},
    ]

    def test_all_day_and_timed_copies_collapse_to_the_timed_one(self) -> None:
        kept = study_brief.dedupe_same_day(self.PAIR)
        self.assertEqual([e["id"] for e in kept], ["b", "c"])

    def test_plan_lists_each_assessment_once(self) -> None:
        with patch.dict(os.environ, dict(_DEFAULT_ENV)):
            briefs = study_brief.plan_briefs(self.PAIR[:2], now=_local(2026, 9, 20, 8))
        self.assertEqual(briefs[0]["items"], [{"title": "CS 3190  Quiz 2 (Lec 5-8)", "days_left": 4, "kind": "assessment"}])

    def test_hub_countdown_lists_each_assessment_once(self) -> None:
        with patch.dict(os.environ, dict(_DEFAULT_ENV)):
            academic = academic_events_from(self.PAIR, now=_local(2026, 9, 20, 8))
        self.assertEqual([e["id"] for e in academic], ["b", "c"])


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, dict(_DEFAULT_ENV))
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def test_plans_one_brief_per_morning_inside_window(self) -> None:
        briefs = study_brief.plan_briefs(EVENTS, now=NOW)
        by_id = {b["id"]: b for b in briefs}
        self.assertEqual(briefs[0]["id"], "2026-09-15")
        self.assertEqual(briefs[0]["items"], [{"title": "CS 3550 Quiz 2", "days_left": 2, "kind": "assessment"}])
        self.assertEqual([(i["title"], i["days_left"]) for i in by_id["2026-09-17"]["items"]],
                         [("CS 3550 Quiz 2", 0), ("Midterm Exam", 13)])
        self.assertNotIn("2026-09-18", {i["title"] for i in by_id["2026-09-18"]["items"]})
        self.assertEqual(by_id["2026-09-30"]["items"], [{"title": "Midterm Exam", "days_left": 0, "kind": "assessment"}])
        self.assertEqual(briefs[-1]["id"], "2026-09-30")
        self.assertEqual(len(briefs), 16)
        self.assertEqual(datetime.fromtimestamp(briefs[0]["fire_at"]).hour, 9)

    def test_hour_already_passed_today_skips_todays_brief(self) -> None:
        briefs = study_brief.plan_briefs(EVENTS, now=_local(2026, 9, 15, 9, 30))
        self.assertEqual(briefs[0]["id"], "2026-09-16")

    def test_timed_assessment_before_fire_hour_is_not_listed_that_morning(self) -> None:
        early = [{"summary": "Quiz 3", "start": _local(2026, 9, 17, 8, 0), "all_day": False}]
        ids = {b["id"] for b in study_brief.plan_briefs(early, now=NOW)}
        self.assertEqual(ids, {"2026-09-15", "2026-09-16"})

    def test_template_body_caps_at_four_lines(self) -> None:
        items = [{"title": f"Quiz {i}", "days_left": i, "kind": "assessment"} for i in range(6)]
        body = study_brief.template_body(items)
        self.assertEqual(body.count("\n"), 4)
        self.assertIn("+2 more in your Hub.", body)
        self.assertTrue(body.startswith("Quiz 0 is today.\nQuiz 1 is tomorrow.\nQuiz 2 is in 2 days."))


class CompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, dict(_DEFAULT_ENV))
        self.env.start()
        study_brief.clear_cache()

    def tearDown(self) -> None:
        self.env.stop()
        study_brief.clear_cache()

    def test_model_writes_the_next_three_and_templates_the_rest(self) -> None:
        client = _FakeClient()
        payload = study_brief.compose(EVENTS, now=NOW, client=client)
        self.assertEqual(payload["composed_by"], "model")
        self.assertEqual(client.calls, 1)
        self.assertEqual([b["body"] for b in payload["briefs"][:3]], ["Brief 0, sir.", "Brief 1, sir.", "Brief 2, sir."])
        self.assertEqual(payload["briefs"][3]["body"], "Midterm Exam is in 12 days.")

    def test_model_failure_falls_back_to_template(self) -> None:
        payload = study_brief.compose(EVENTS, now=NOW, client=_FakeClient(fail=True))
        self.assertEqual(payload["composed_by"], "template")
        self.assertEqual(payload["briefs"][0]["body"], "CS 3550 Quiz 2 is in 2 days.")

    def test_wrong_count_from_model_falls_back(self) -> None:
        payload = study_brief.compose(EVENTS, now=NOW, client=_FakeClient(bodies=["only one"]))
        self.assertEqual(payload["composed_by"], "template")

    def test_disabled_returns_no_briefs_without_model_call(self) -> None:
        client = _FakeClient()
        with patch.dict(os.environ, {"STUDY_BRIEF_ENABLED": "off"}):
            payload = study_brief.compose(EVENTS, now=NOW, client=client)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["briefs"], [])
        self.assertEqual(client.calls, 0)

    def test_style_note_reaches_the_model_prompt(self) -> None:
        captured = {}

        class Spy(_FakeClient):
            def create(self, **kwargs):
                captured.update(kwargs)
                return super().create(**kwargs)

        with patch.dict(os.environ, {"STUDY_BRIEF_STYLE": "no jokes before coffee", "ADDRESS": "boss"}):
            study_brief.compose(EVENTS, now=NOW, client=Spy())
        self.assertIn("no jokes before coffee", captured["system"])
        self.assertIn('"boss"', captured["system"])

    def test_cache_reuses_until_settings_or_events_change(self) -> None:
        client = _FakeClient()
        with patch.object(study_brief, "_get_client", return_value=client):
            study_brief.compose_cached(EVENTS, now=NOW)
            study_brief.compose_cached(EVENTS, now=NOW)
            self.assertEqual(client.calls, 1)
            with patch.dict(os.environ, {"STUDY_BRIEF_HOUR": "7"}):
                payload = study_brief.compose_cached(EVENTS, now=NOW)
            self.assertEqual(client.calls, 2)
            self.assertEqual(payload["hour"], 7)
            study_brief.compose_cached(EVENTS + [{"summary": "Final", "start": _local(2026, 9, 20)}], now=NOW)
            self.assertEqual(client.calls, 3)

    def test_spoken_preview_reads_settings_and_next_brief(self) -> None:
        payload = study_brief.compose(EVENTS, now=NOW, client=_FakeClient(fail=True))
        spoken = study_brief.spoken_preview(payload)
        self.assertIn("fires daily at 9 AM", spoken)
        self.assertIn("14-day warning for assessments", spoken)
        self.assertIn("7-day warning for assignments", spoken)
        self.assertIn("CS 3550 Quiz 2 is in 2 days.", spoken)
        self.assertIn("could not be reached", study_brief.spoken_preview(payload, "Calendar could not be reached."))


class StudyBriefEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.clear_cache()
        study_brief.clear_cache()
        self.env = patch.dict(os.environ, {**_DEFAULT_ENV, "JARVIS_API_TOKEN": "test-token"})
        self.env.start()
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        status_hub.clear_cache()
        study_brief.clear_cache()

    def test_requires_auth(self) -> None:
        self.assertEqual(self.client.get("/api/study/brief").status_code, 401)

    def test_unconfigured_calendar_reports_error_and_settings(self) -> None:
        with patch.object(status_hub, "calendar_configured", return_value=False):
            body = self.client.get("/api/study/brief", headers=self.headers).json()
        self.assertEqual(body["error"], "Calendar is not configured.")
        self.assertEqual(body["hour"], 9)
        self.assertEqual(body["briefs"], [])

    def test_composed_briefs_shape(self) -> None:
        # The endpoint plans from the real clock, so the fixture must move with
        # it: the same events, shifted so the quiz is always two days out.
        shift = (datetime.now().astimezone() + timedelta(days=2)).date() - EVENTS[0]["start"].date()
        events = [dict(e, start=e["start"] + shift) for e in EVENTS]
        with patch.object(status_hub, "calendar_configured", return_value=True), \
             patch.object(status_hub, "upcoming_events", return_value=events) as calendar, \
             patch.object(study_brief, "_get_client", return_value=None):
            body = self.client.get("/api/study/brief", headers=self.headers).json()
            self.client.get("/api/study/brief", headers=self.headers)
        calendar.assert_called_once_with(max_results=2500, hours_ahead=1440, allow_interactive=False)
        self.assertEqual(body["error"], "")
        self.assertTrue(body["enabled"])
        first = body["briefs"][0]
        self.assertEqual(set(first), {"id", "fire_at", "items", "body"})
        self.assertIn("CS 3550 Quiz 2", first["body"])
        self.assertIn("study_brief", server._COMPANION_CAPABILITIES["dashboard"])


if __name__ == "__main__":
    unittest.main()
