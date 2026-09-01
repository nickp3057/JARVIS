"""Persistent, bounded, explicitly retrieved conversation summaries."""

from __future__ import annotations

import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import brain.history as history
from brain.tools import execute_tool, get_tool_specs


class SessionSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_path = history._DB_PATH
        history._DB_PATH = Path(self.tempdir.name) / "history.db"

    def tearDown(self) -> None:
        history._DB_PATH = self.old_path
        self.tempdir.cleanup()

    def test_append_turn_maintains_bounded_session_digest(self) -> None:
        session_id = history.start_new_session()
        for number in range(8):
            history.append_turn(
                f"Question {number} about chemistry " + "x" * 350,
                f"Answer {number} " + "y" * 350,
                session_id,
            )
        session = history.list_sessions()[0]
        self.assertLessEqual(len(session["summary"]), history.MAX_SESSION_SUMMARY_CHARS)
        self.assertIn("Question 7", session["summary"])
        self.assertIn("JARVIS: Answer 7", session["summary"])
        self.assertIsNotNone(session["summarized_at"])

    def test_search_ranks_relevant_summaries_and_supports_recent_listing(self) -> None:
        chemistry = history.start_new_session()
        history.append_turn("Plan the chemistry lab", "We chose titration.", chemistry)
        movies = history.start_new_session()
        history.append_turn("Find a movie", "We selected Arrival.", movies)

        matches = history.search_session_summaries("titration chemistry")
        self.assertEqual(matches[0]["session_id"], chemistry)
        self.assertNotIn(movies, [item["session_id"] for item in matches])
        recent = history.search_session_summaries(limit=1)
        self.assertEqual(recent[0]["session_id"], movies)

    def test_opt_in_local_summary_replaces_digest_when_valid(self) -> None:
        session_id = history.start_new_session()
        with patch.dict(os.environ, {"LOCAL_MODEL_SUMMARIES": "true"}), patch(
            "brain.local_model.generate_summary", return_value="Concise local digest."
        ) as summarize:
            history.append_turn("A long question", "A useful answer", session_id)
        self.assertEqual(history.list_sessions()[0]["summary"], "Concise local digest.")
        summarize.assert_called_once()

    def test_local_summary_failure_retains_deterministic_digest(self) -> None:
        session_id = history.start_new_session()
        with patch.dict(os.environ, {"LOCAL_MODEL_SUMMARIES": "true"}), patch(
            "brain.local_model.generate_summary", return_value=None,
        ):
            history.append_turn("Keep this question", "Keep this answer", session_id)
        summary = history.list_sessions()[0]["summary"]
        self.assertIn("Nick: Keep this question", summary)
        self.assertIn("JARVIS: Keep this answer", summary)

    def test_summary_tool_is_explicit_and_returns_session_ids(self) -> None:
        session_id = history.start_new_session()
        history.append_turn("Discuss Apollo", "Reviewed the launch plan.", session_id)
        names = {spec.get("name") for spec in get_tool_specs()}
        self.assertIn("search_session_summaries", names)
        result = execute_tool("search_session_summaries", {"query": "Apollo"})
        self.assertIn(session_id, result)
        self.assertIn("Reviewed the launch plan", result)

    def test_deleted_and_cleared_sessions_remove_summaries(self) -> None:
        first = history.start_new_session()
        history.append_turn("Temporary", "Gone soon", first)
        self.assertTrue(history.delete_session(first))
        self.assertEqual(history.search_session_summaries(), [])

        second = history.start_new_session()
        history.append_turn("Another", "Also gone", second)
        history.clear_history()
        self.assertEqual(history.search_session_summaries(), [])


if __name__ == "__main__":
    unittest.main()
