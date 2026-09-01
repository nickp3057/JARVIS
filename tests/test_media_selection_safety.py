import unittest
from unittest.mock import patch

from integrations import media_selections, radarr, sonarr


class MediaSelectionSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        media_selections.clear_for_tests()

    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    @patch("integrations.radarr._lookup")
    def test_movie_wrong_id_is_blocked_before_second_lookup(self, lookup, _configured) -> None:
        lookup.return_value = (True, [{"title": "The Martian", "year": 2015, "tmdbId": 286217}])
        radarr.find_movie("The Martian", session_id="phone")
        result = radarr.add_movie(99861, confirmed=True, session_id="phone")
        self.assertIn("not in the latest results", result)
        lookup.assert_called_once_with("The Martian")

    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    @patch("integrations.radarr._lookup")
    def test_movie_receipt_is_session_bound(self, lookup, _configured) -> None:
        lookup.return_value = (True, [{"title": "The Martian", "tmdbId": 286217}])
        radarr.find_movie("The Martian", session_id="phone")
        result = radarr.add_movie(286217, confirmed=True, session_id="other")
        self.assertIn("fresh lookup", result)

    @patch("integrations.radarr.servarr.request")
    @patch("integrations.radarr.servarr.resolve_root_folder", return_value=(1, "/movies"))
    @patch("integrations.radarr.servarr.resolve_quality", return_value=(2, "HD"))
    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    @patch("integrations.radarr._lookup")
    def test_exact_confirmed_movie_can_be_added_once(
        self, lookup, _configured, _quality, _root, request
    ) -> None:
        movie = {"title": "The Martian", "year": 2015, "tmdbId": 286217}
        lookup.return_value = (True, [movie])
        request.return_value = (True, {"id": 1})
        radarr.find_movie("The Martian", session_id="phone")
        result = radarr.add_movie(286217, confirmed=True, session_id="phone")
        self.assertIn("Added The Martian", result)
        self.assertEqual(request.call_count, 1)
        replay = radarr.add_movie(286217, confirmed=True, session_id="phone")
        self.assertIn("fresh lookup", replay)

    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    def test_confirmation_is_still_required(self, _configured) -> None:
        result = radarr.add_movie(286217, confirmed=False, session_id="phone")
        self.assertIn("confirm", result)

    @patch("integrations.sonarr.servarr.is_configured", return_value=True)
    @patch("integrations.sonarr._lookup")
    def test_series_wrong_id_is_blocked(self, lookup, _configured) -> None:
        lookup.return_value = (True, [{"title": "Severance", "tvdbId": 371980}])
        sonarr.find_series("Severance", session_id="phone")
        result = sonarr.add_series(999, confirmed=True, session_id="phone")
        self.assertIn("not in the latest results", result)
        lookup.assert_called_once_with("Severance")

    @patch("integrations.media_selections.time.monotonic", side_effect=[0, 901])
    def test_receipt_expires(self, _clock) -> None:
        media_selections.record("radarr", "phone", [{"tmdbId": 1}], "tmdbId")
        allowed, reason = media_selections.validate("radarr", "phone", 1)
        self.assertFalse(allowed)
        self.assertIn("expired", reason)


if __name__ == "__main__":
    unittest.main()
