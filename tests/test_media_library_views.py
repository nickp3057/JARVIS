"""Read-only Radarr and Sonarr history/missing/upcoming views."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from brain.tools import execute_tool, get_tool_specs
from integrations import radarr, sonarr


class MediaLibraryViewTests(unittest.TestCase):
    @patch("integrations.radarr.servarr.request")
    def test_radarr_history_missing_and_upcoming_are_bounded(self, request) -> None:
        request.side_effect = [
            (True, {"records": [{"movie": {"title": "Arrival"}, "eventType": "downloadFolderImported", "date": "today"}]}),
            (True, {"records": [{"title": "Dune", "year": 2021}]}),
            (True, [{"title": "Tron", "digitalRelease": "tomorrow"}]),
        ]
        self.assertIn("Arrival", radarr.history(500))
        self.assertEqual(request.call_args_list[0].kwargs["params"]["pageSize"], 50)
        self.assertIn("Dune (2021)", radarr.missing(10))
        self.assertIn("Tron: tomorrow", radarr.upcoming(500))
        self.assertIn("end", request.call_args_list[2].kwargs["params"])

    @patch("integrations.sonarr.servarr.request")
    def test_sonarr_views_format_episode_identity(self, request) -> None:
        episode = {"seasonNumber": 2, "episodeNumber": 3, "title": "The Test"}
        request.side_effect = [
            (True, {"records": [{"series": {"title": "Severance"}, "episode": episode, "eventType": "grabbed", "date": "today"}]}),
            (True, {"records": [{**episode, "series": {"title": "Severance"}}]}),
            (True, [{**episode, "series": {"title": "Severance"}, "airDateUtc": "tomorrow"}]),
        ]
        self.assertIn("Severance S02E03", sonarr.history())
        self.assertIn("Severance S02E03: The Test", sonarr.missing())
        self.assertIn("Severance S02E03: The Test — tomorrow", sonarr.upcoming())

    def test_empty_malformed_and_failed_payloads_are_safe(self) -> None:
        with patch("integrations.radarr.servarr.request", return_value=(True, {"records": []})):
            self.assertIn("No recent", radarr.history())
        with patch("integrations.sonarr.servarr.request", return_value=(True, "bad")):
            self.assertIn("No monitored", sonarr.missing())
        with patch("integrations.radarr.servarr.request", return_value=(False, "offline")):
            self.assertEqual(radarr.upcoming(), "offline")

    def test_tools_are_registered_and_validate_service(self) -> None:
        names = {item.get("name") for item in get_tool_specs()}
        self.assertTrue({"media_history", "missing_media", "upcoming_media"}.issubset(names))
        self.assertIn("Choose radarr or sonarr", execute_tool("media_history", {"service": "bad"}))
        with patch("integrations.radarr.history", return_value="history") as history:
            self.assertEqual(execute_tool("media_history", {"service": "radarr", "limit": 4}), "history")
        history.assert_called_once_with(4)


if __name__ == "__main__":
    unittest.main()
