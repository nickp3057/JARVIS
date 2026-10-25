"""Servarr client reliability: retries, error classification, matching, years."""
import os
import unittest
from unittest.mock import Mock, patch

import requests

from integrations import radarr, servarr_common as servarr, media_selections

_ENV = {"RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k"}


def _response(status: int, body=None, text: str = "") -> Mock:
    response = Mock(status_code=status, text=text)
    if body is None:
        response.json.side_effect = ValueError
    else:
        response.json.return_value = body
    return response


class RequestTests(unittest.TestCase):
    def setUp(self) -> None:
        servarr._sessions.clear()
        self.session = Mock(headers={})
        patcher = patch.object(servarr.requests, "Session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, _ENV)
        env.start()
        self.addCleanup(env.stop)
        sleep = patch.object(servarr.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_get_retries_once_after_connection_error(self) -> None:
        self.session.request.side_effect = [requests.ConnectionError("x"), _response(200, [1])]
        self.assertEqual(servarr.call("radarr", "GET", "/api/v3/movie"), servarr.Result(True, [1], "ok"))

    def test_post_is_never_retried(self) -> None:
        self.session.request.side_effect = requests.ReadTimeout("slow")
        result = servarr.call("radarr", "POST", "/api/v3/movie", json={})
        self.assertEqual(result.kind, "timeout")
        self.assertEqual(self.session.request.call_count, 1)

    def test_unreachable_message_is_plain_english(self) -> None:
        self.session.request.side_effect = requests.ConnectTimeout("pool")
        result = servarr.call("radarr", "GET", "/x")
        self.assertEqual(result.kind, "unreachable")
        self.assertIn("can't reach Radarr", result.payload)
        self.assertNotIn("pool", result.payload)

    def test_bad_key_is_named(self) -> None:
        self.session.request.return_value = _response(401, {})
        result = servarr.call("radarr", "GET", "/x")
        self.assertEqual(result.kind, "auth")
        self.assertIn("RADARR_API_KEY", result.payload)

    def test_validation_errors_are_extracted(self) -> None:
        self.session.request.return_value = _response(400, [{"errorMessage": "Path is not writable"}])
        result = servarr.call("radarr", "POST", "/api/v3/movie", json={})
        self.assertEqual(result.kind, "rejected")
        self.assertIn("Path is not writable", result.payload)

    def test_profile_cache_expires(self) -> None:
        self.session.request.return_value = _response(200, [{"id": 1, "name": "HD"}])
        servarr._profile_cache.clear()
        with patch.object(servarr.time, "monotonic", side_effect=[0, 10, 10_000, 10_000]):
            servarr.get_quality_profiles("radarr")
            servarr.get_quality_profiles("radarr")
            servarr.get_quality_profiles("radarr")
        self.assertEqual(self.session.request.call_count, 2)


class MatchingTests(unittest.TestCase):
    def test_most_specific_profile_wins(self) -> None:
        self.assertEqual(servarr._best_match("720p", ["HD - 720p/1080p", "HD-720p"]), "HD-720p")

    def test_wrong_resolution_is_never_chosen(self) -> None:
        self.assertIsNone(servarr._best_match("720p", ["HD-1080p", "Ultra-HD"]))
        self.assertEqual(servarr._best_match("4k", ["HD-1080p", "Ultra-HD 2160p"]), "Ultra-HD 2160p")

    def test_tie_is_ambiguous(self) -> None:
        self.assertIsNone(servarr._best_match("hd", ["HD-720p", "HD-1080"]))

    def test_trailing_year_split(self) -> None:
        self.assertEqual(servarr.split_trailing_year("Dune (2021)"), ("Dune", 2021))
        self.assertEqual(servarr.split_trailing_year("2001: A Space Odyssey"), ("2001: A Space Odyssey", None))


class TitleLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        media_selections.clear_for_tests()

    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    @patch("integrations.radarr._lookup")
    def test_year_narrows_to_one_title(self, lookup, _configured) -> None:
        lookup.return_value = (True, [
            {"title": "Dune", "year": 1984, "tmdbId": 841},
            {"title": "Dune", "year": 2021, "tmdbId": 438631},
        ])
        reply = radarr.find_movie("Dune 2021", session_id="s")
        lookup.assert_called_once_with("Dune")
        self.assertIn("One match", reply)
        self.assertIn("438631", reply)
        self.assertFalse(media_selections.validate("radarr", "s", 841)[0])

    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    @patch("integrations.radarr._lookup")
    def test_numeric_title_falls_back_to_literal_search(self, lookup, _configured) -> None:
        literal = {"title": "Blade Runner 2049", "year": 2017, "tmdbId": 335984}
        lookup.side_effect = [(True, [{"title": "Blade Runner", "year": 1982, "tmdbId": 78}]), (True, [literal])]
        reply = radarr.find_movie("Blade Runner 2049", session_id="s")
        self.assertEqual(lookup.call_args_list[1].args, ("Blade Runner 2049",))
        self.assertIn("335984", reply)

    @patch("integrations.radarr.servarr.resolve_root_folder", return_value=("/movies", "/movies"))
    @patch("integrations.radarr.servarr.resolve_quality", return_value=(2, "HD-720p"))
    @patch("integrations.radarr.servarr.is_configured", return_value=True)
    @patch("integrations.radarr._lookup")
    def test_timed_out_add_is_verified_not_retried(self, lookup, _configured, _q, _r) -> None:
        movie = {"title": "The Martian", "year": 2015, "tmdbId": 286217}
        lookup.side_effect = [(True, [movie]), (True, [movie]), (True, [dict(movie, id=9)])]
        radarr.find_movie("The Martian", session_id="s")
        timeout = servarr.Result(False, "Radarr didn't answer in time.", "timeout")
        with patch.object(radarr.servarr, "call", return_value=timeout) as call:
            reply = radarr.add_movie(286217, confirmed=True, session_id="s")
        self.assertEqual(call.call_count, 1)
        self.assertIn("Added The Martian", reply)


if __name__ == "__main__":
    unittest.main()
