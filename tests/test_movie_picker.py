import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server
from integrations import plex


def _item(key, title, year, added, **extra):
    return {"type": "movie", "ratingKey": key, "title": title, "year": year, "addedAt": added,
            "thumb": f"/library/metadata/{key}/thumb/1", "duration": 6_000_000, **extra}


class LibraryTests(unittest.TestCase):
    def test_merges_libraries_once_each_in_order_with_resume_points(self):
        pages = {
            "/library/sections": {"MediaContainer": {"Directory": [{"key": "1", "type": "movie"},
                                                                   {"key": "2", "type": "show"},
                                                                   {"key": "3", "type": "movie"}]}},
            "/library/sections/1/all": {"MediaContainer": {"Metadata": [
                _item("10", "Dune", 2021, 100, viewOffset=600_000), _item("11", "Heat", 1995, 50, viewCount=2)]}},
            "/library/sections/3/all": {"MediaContainer": {"Metadata": [
                _item("30", "Dune", 2021, 300), _item("31", "Arrival", 2016, 200)]}},
        }
        calls = []

        def fake_get(path, params=None):
            calls.append((path, params))
            return True, pages[path]

        with patch.object(plex, "_get", side_effect=fake_get):
            ok, films = plex.library_movies(sort="recent")
        self.assertTrue(ok)
        self.assertEqual([f["title"] for f in films], ["Arrival", "Dune", "Heat"])  # 4K Dune skipped, newest first
        dune = films[1]
        self.assertEqual((dune["rating_key"], dune["view_offset_ms"]), ("10", 600_000))
        self.assertTrue(films[2]["watched"])
        with patch.object(plex, "_get", side_effect=fake_get):
            plex.library_movies(query="dune", sort="unwatched")
        params = calls[-1][1]
        self.assertEqual((params["title"], params["unwatched"]), ("dune", 1))

    def test_poster_only_serves_library_artwork(self):
        with patch.dict(os.environ, {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t"}):
            for bad in ("/etc/passwd", "/library/metadata/../../x", "http://evil/x", "/library/metadata/1?x=y"):
                self.assertFalse(plex.poster(bad)[0], bad)
            response = MagicMock(status_code=200, content=b"jpeg", headers={"Content-Type": "image/jpeg"})
            with patch.object(plex.requests, "get", return_value=response) as get:
                ok, body, kind = plex.poster("/library/metadata/10/thumb/1", 2000)
        self.assertEqual((ok, body, kind), (True, b"jpeg", "image/jpeg"))
        self.assertEqual(get.call_args.kwargs["params"]["width"], 800)  # clamped


class EndpointTests(unittest.TestCase):
    def setUp(self):
        token = patch.dict(os.environ, {"JARVIS_API_TOKEN": "test-token", "PLEX_URL": "http://plex", "PLEX_TOKEN": "t"})
        token.start()
        self.addCleanup(token.stop)
        server._runtime = MagicMock()
        self.addCleanup(setattr, server, "_runtime", None)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer test-token"}

    def test_play_resumes_or_starts_over_on_the_tv(self):
        record = {"title": "Dune", "rating_key": "10"}
        with patch.object(plex, "movie", return_value=(True, record)), \
             patch.object(plex, "server_id", return_value="srv"), \
             patch.object(plex, "play_from_start", return_value=(True, "")) as reset, \
             patch("integrations.tv_remote.play_plex", return_value=(True, "")) as play:
            body = self.client.post("/api/plex/play", json={"rating_key": "10"}, headers=self.headers).json()
            reset.assert_not_called()
            self.client.post("/api/plex/play", json={"rating_key": "10", "from_start": True}, headers=self.headers)
            reset.assert_called_once_with("10")
        self.assertEqual(body["message"], "Starting Dune on the TV.")
        play.assert_called_with("10", "srv")
        bad = self.client.post("/api/plex/play", json={"rating_key": "10; rm"}, headers=self.headers)
        self.assertEqual(bad.status_code, 422)

    def test_movies_and_poster_routes(self):
        with patch.object(plex, "library_movies", return_value=(True, [{"title": "Dune"}])):
            self.assertEqual(self.client.get("/api/plex/movies", headers=self.headers).json()["movies"], [{"title": "Dune"}])
        with patch.object(plex, "poster", return_value=(True, b"img", "image/jpeg")):
            response = self.client.get("/api/plex/poster", params={"path": "/library/metadata/1/thumb/2"}, headers=self.headers)
        self.assertEqual((response.status_code, response.content), (200, b"img"))
        self.assertEqual(self.client.get("/api/plex/movies").status_code, 401)


if __name__ == "__main__":
    unittest.main()
