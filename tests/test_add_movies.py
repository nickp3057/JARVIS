"""Batch movie adds (add_movies) and the arr-update auto retry."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brain import arr_stack
from integrations import qbittorrent as qbit
from integrations import radarr


def _movie(title, tmdb, year=2000, **extra):
    return {"title": title, "tmdbId": tmdb, "year": year, **extra}


LOOKUPS = {
    "Good Will Hunting": [_movie("Good Will Hunting", 489, 1997)],
    "Oppenheimer": [_movie("Oppenheimer", 872585, 2023, id=7)],  # already in
    "Gold": [_movie("Gold", 1, 2016), _movie("Gold", 2, 2014)],  # ambiguous
    "Nothing": [],
    "Dune": [_movie("Dune", 438631, 2021), _movie("Dune", 841, 1984)],  # same title twice
    "Heat": [_movie("Heat", 949, 1995), _movie("Heat Wave", 5, 2022)],  # top title unique
}


class AddMoviesTests(unittest.TestCase):
    def run_batch(self, titles, confirmed=True):
        posted = []
        def call(service, method, path, json=None):
            posted.append(json["tmdbId"])
            return SimpleNamespace(ok=True, kind="", payload=None)
        with patch.object(radarr.servarr, "is_configured", return_value=True), \
             patch.object(radarr.servarr, "resolve_quality", return_value=(4, "Standard")), \
             patch.object(radarr.servarr, "resolve_root_folder", return_value=("/movies", "")), \
             patch.object(radarr, "candidates", side_effect=lambda t: (True, LOOKUPS[t])), \
             patch.object(radarr.servarr, "call", side_effect=call):
            return radarr.add_movies(titles, confirmed=confirmed), posted

    def test_adds_plain_matches_and_reports_the_rest(self):
        reply, posted = self.run_batch(["Good Will Hunting", "Oppenheimer", "Gold", "Nothing", "Dune", "Heat"])
        self.assertEqual(posted, [489, 949])
        self.assertIn("Added 2 on the Standard profile", reply)
        self.assertIn("Already in the library: Oppenheimer (2023)", reply)
        self.assertIn("Gold: several matches, not added", reply)
        self.assertIn("Nothing: no match", reply)
        self.assertIn("Dune: several matches", reply)

    def test_needs_confirmation_and_titles(self):
        self.assertIn("confirm", self.run_batch(["Good Will Hunting"], confirmed=False)[0])
        self.assertIn("Which movies", self.run_batch([])[0])
        self.assertEqual(self.run_batch("Good Will Hunting; good will hunting")[1], [489])


class CleanTitleTests(unittest.TestCase):
    def test_download_status_says_the_title_not_the_release(self):
        torrents = [{"name": "Good.Will.Hunting.1997.1080p.BluRay.x264.Atmos-GROUP", "progress": 0.5,
                     "dlspeed": 0, "eta": 60, "state": "downloading", "size": 0}]
        with patch.object(qbit, "torrent_info", return_value=(True, torrents)), \
             patch.object(qbit, "transfer_info", return_value=(False, None)):
            result = qbit.download_status()
        self.assertIn("Good Will Hunting: 50.0%", result)
        self.assertNotIn("x264", result)


class AutoRetryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        patcher = patch.object(arr_stack, "_RETRY_FILE", Path(self.dir.name) / "retry.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    @staticmethod
    def health(text):
        return {"updater": {"last_run": "2026-11-22 05:00:01", "mtime": 1, "applied": [], "text": text}}

    def test_failed_run_is_retried_then_alerted_once(self):
        failed = self.health("Last run: x\nFAILED: pull-qbittorrent (exit 1)\n")
        with patch.object(arr_stack, "start_update", return_value="started") as start, \
             patch.object(arr_stack, "recovery_in_progress", return_value=False):
            now = 1_000_000.0
            for i in range(3):
                self.assertIsNone(arr_stack.auto_retry_update(failed, now + i * 7300))
                self.assertIsNone(arr_stack.auto_retry_update(failed, now + i * 7300 + 300))  # too soon
            self.assertEqual(start.call_count, 3)
            alert = arr_stack.auto_retry_update(failed, now + 30000)
            self.assertIn("keeps failing", alert)
            self.assertIsNone(arr_stack.auto_retry_update(failed, now + 30300))
            self.assertEqual(start.call_count, 3)

    def test_success_clears_state_and_never_retries(self):
        with patch.object(arr_stack, "start_update") as start:
            self.assertIsNone(arr_stack.auto_retry_update(self.health("SUCCESS: update completed\n"), 5.0))
        start.assert_not_called()

    def test_partial_run_counts_as_a_failure(self):
        partial = self.health("PULL FAILED: qbittorrent\nPARTIAL: could not pull qbittorrent; everything else updated\n")
        with patch.object(arr_stack, "start_update", return_value="ok") as start, \
             patch.object(arr_stack, "recovery_in_progress", return_value=False):
            arr_stack.auto_retry_update(partial, 10.0)
        start.assert_called_once()
        self.assertEqual(len(json.loads(arr_stack._RETRY_FILE.read_text())["retries"]), 1)


if __name__ == "__main__":
    unittest.main()
