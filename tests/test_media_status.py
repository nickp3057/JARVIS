"""Tests for Radarr/Sonarr queue and health summaries."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from integrations import servarr_common as servarr


class MediaStatusTests(unittest.TestCase):
    def test_radarr_queue_formats_title_progress_eta_and_error(self) -> None:
        payload = {
            "records": [{
                "movie": {"title": "Example Movie"},
                "size": 10 * 1024**3,
                "sizeleft": 4 * 1024**3,
                "timeleft": "01:20:00",
                "trackedDownloadStatus": "warning",
                "errorMessage": "Not enough seeders",
            }],
        }
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_queue", return_value=(True, payload),
        ):
            result = servarr.queue_status("radarr")
        self.assertIn("Example Movie: 60.0%", result)
        self.assertIn("4.0 GB left", result)
        self.assertIn("Not enough seeders", result)

    def test_sonarr_queue_includes_episode_title(self) -> None:
        payload = {"records": [{
            "series": {"title": "Example Show"},
            "episode": {"title": "Pilot"},
            "size": 100,
            "sizeleft": 0,
            "status": "completed",
        }]}
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_queue", return_value=(True, payload),
        ):
            result = servarr.queue_status("sonarr")
        self.assertIn("Example Show — Pilot", result)

    def test_health_reports_clear_and_warning_states(self) -> None:
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr,
            "get_health",
            side_effect=[(True, []), (True, [{"type": "warning", "message": "Indexer unavailable"}])],
        ):
            result = servarr.health_status()
        self.assertIn("Radarr reports no health warnings", result)
        self.assertIn("Indexer unavailable", result)

    def test_queue_handles_unconfigured_and_unreachable_services(self) -> None:
        with patch.object(servarr, "is_configured", return_value=False):
            self.assertIn("not configured", servarr.queue_status("radarr"))
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_queue", return_value=(False, "Radarr offline"),
        ):
            self.assertEqual(servarr.queue_status("radarr"), "Radarr offline")

    def test_queue_tolerates_malformed_response_and_zero_size(self) -> None:
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_queue", return_value=(True, ["unexpected"]),
        ):
            self.assertIn("queue is clear", servarr.queue_status("radarr"))
        payload = {"records": [{"title": "Metadata", "size": 0, "sizeleft": 0}]}
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_queue", return_value=(True, payload),
        ):
            self.assertIn("Metadata: 0.0%", servarr.queue_status("radarr"))

    def test_queue_request_clamps_page_size_and_sets_service_flags(self) -> None:
        with patch.object(servarr, "request", return_value=(True, {})) as request:
            servarr.get_queue("sonarr", page_size=1000)
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["pageSize"], 200)
        self.assertEqual(params["includeSeries"], "true")
        self.assertEqual(params["includeEpisode"], "true")

    def test_health_handles_unconfigured_error_and_malformed_payload(self) -> None:
        with patch.object(servarr, "is_configured", return_value=False):
            self.assertIn("not configured", servarr.health_status("sonarr"))
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_health", return_value=(False, "Sonarr offline"),
        ):
            self.assertEqual(servarr.health_status("sonarr"), "Sonarr offline")
        with patch.object(servarr, "is_configured", return_value=True), patch.object(
            servarr, "get_health", return_value=(True, {"unexpected": True}),
        ):
            self.assertIn("no health warnings", servarr.health_status("sonarr"))

    def test_format_bytes_handles_bad_and_large_values(self) -> None:
        self.assertEqual(servarr._format_bytes("bad"), "unknown")
        self.assertEqual(servarr._format_bytes(1024**4), "1.0 TB")


if __name__ == "__main__":
    unittest.main()
