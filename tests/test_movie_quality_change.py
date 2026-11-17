"""change_movie_quality: downgrade deletes file + torrent, upgrade only switches."""
import os
import unittest
from unittest.mock import patch

from brain import confirm_gate
from integrations import radarr
from integrations import servarr_common as servarr

HASH = "ab" * 20


def _group(name, ids, allowed):
    return {"name": name, "allowed": allowed,
            "items": [{"quality": {"id": i, "name": n}} for i, n in ids]}


PROFILES = [
    {"id": 11, "name": "Standard", "items": [
        _group("1080p Encodes", [(7, "Bluray-1080p"), (3, "WEBDL-1080p")], True),
        _group("2160p Encodes", [(19, "Bluray-2160p"), (18, "WEBDL-2160p")], False)]},
    {"id": 12, "name": "Favorite 4K", "items": [
        _group("1080p Encodes", [(7, "Bluray-1080p"), (3, "WEBDL-1080p")], True),
        _group("2160p Encodes", [(19, "Bluray-2160p"), (18, "WEBDL-2160p")], True)]},
]


def movie(profile_id, quality_id, quality_name):
    return {"id": 5, "title": "Weapons", "year": 2025, "qualityProfileId": profile_id, "hasFile": True,
            "movieFile": {"id": 77, "path": "/movies/Weapons (2025)/Weapons.mkv",
                          "quality": {"quality": {"id": quality_id, "name": quality_name}}}}


class ChangeMovieQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        confirm_gate.clear_for_tests()
        env = patch.dict(os.environ, {"RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k",
                                      "RADARR_QUALITY_MAP": ""})
        env.start()
        self.addCleanup(env.stop)
        self.movie = movie(12, 19, "Bluray-2160p")
        self.calls: list[tuple[str, str]] = []
        for target, kwargs in (
            (radarr, {"_lookup": lambda term: (True, [{"title": "Weapons", "year": 2025, "tmdbId": 1, "id": 5},
                                                       {"title": "Weapons", "year": 1999, "tmdbId": 2}])}),
            (servarr, {"get_quality_profiles": lambda *_a, **_k: (True, PROFILES),
                       "call": self.fake_call}),
            (radarr.qbittorrent, {"is_configured": lambda: True}),
        ):
            for name, fn in kwargs.items():
                p = patch.object(target, name, side_effect=fn)
                p.start()
                self.addCleanup(p.stop)
        self.qbit = patch.object(radarr.qbittorrent, "manage_torrent", return_value="Delete accepted for x.")
        self.manage = self.qbit.start()
        self.addCleanup(self.qbit.stop)
        trusted = patch("brain.voice_trust.trusted", return_value=True)
        trusted.start()
        self.addCleanup(trusted.stop)

    def fake_call(self, service, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "GET" and path == "/api/v3/movie/5":
            return servarr.Result(True, self.movie, "ok")
        if method == "GET" and path == "/api/v3/history/movie":
            return servarr.Result(True, [
                {"eventType": "grabbed", "downloadId": "FF" * 20, "date": "2026-01-01"},
                {"eventType": "downloadFolderImported", "downloadId": HASH.upper(), "date": "2026-01-02",
                 "data": {"importedPath": "/movies/Weapons (2025)/Weapons.mkv"}}], "ok")
        if method == "GET" and path == "/api/v3/qualityprofile":
            return servarr.Result(True, PROFILES, "ok")
        return servarr.Result(True, {}, "ok")

    def test_downgrade_deletes_file_and_torrent_then_searches(self) -> None:
        reply = radarr.change_movie_quality("Weapons", "standard", session_id="s")
        writes = [c for c in self.calls if c[0] != "GET"]
        self.assertEqual(writes, [("PUT", "/api/v3/movie/5"), ("DELETE", "/api/v3/moviefile/77"),
                                  ("POST", "/api/v3/command")])
        self.manage.assert_called_once_with("delete", HASH, confirmed=True, delete_files=True)
        self.assertIn("Standard", reply)
        self.assertIn("qBittorrent", reply)

    def test_upgrade_keeps_file(self) -> None:
        self.movie = movie(11, 7, "Bluray-1080p")
        radarr.change_movie_quality("Weapons", "4k", session_id="s")
        writes = [c for c in self.calls if c[0] != "GET"]
        self.assertEqual(writes, [("PUT", "/api/v3/movie/5"), ("POST", "/api/v3/command")])
        self.manage.assert_not_called()

    def test_same_profile_is_a_no_op(self) -> None:
        reply = radarr.change_movie_quality("Weapons", "favorite 4k", session_id="s")
        self.assertIn("already", reply)
        self.assertFalse([c for c in self.calls if c[0] != "GET"])

    def test_guest_voice_only_gets_a_proposal(self) -> None:
        with patch("brain.voice_trust.trusted", return_value=False):
            reply = radarr.change_movie_quality("Weapons", "standard", session_id="s")
        self.assertIn("Proposed", reply)
        self.assertFalse([c for c in self.calls if c[0] != "GET"])

    def test_not_in_library(self) -> None:
        with patch.object(radarr, "_lookup", return_value=(True, [{"title": "Weapons", "tmdbId": 2}])):
            reply = radarr.change_movie_quality("Weapons", "standard", session_id="s")
        self.assertIn("isn't in your Radarr library", reply)


if __name__ == "__main__":
    unittest.main()
