import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations import plex
from integrations import servarr_common as servarr
from integrations import temp_4k

LIBRARY = [
    {"id": 1, "tmdbId": 671, "title": "Harry Potter and the Sorcerer's Stone", "year": 2001,
     "movieFile": {"quality": {"quality": {"resolution": 1080}}}},
    {"id": 2, "tmdbId": 672, "title": "Harry Potter and the Chamber of Secrets", "year": 2002,
     "movieFile": {"quality": {"quality": {"resolution": 1080}}}},
    {"id": 3, "tmdbId": 155, "title": "The Dark Knight", "year": 2008,
     "movieFile": {"quality": {"quality": {"resolution": 2160}}}},
    {"id": 4, "tmdbId": 157336, "title": "Interstellar", "year": 2014,
     "movieFile": {"quality": {"quality": {"name": "Remux-2160p", "resolution": 2160, "modifier": "remux"}}}},
]


class FakeArr:
    """Both Radarrs: the main library and a 4K instance that starts empty."""

    def __init__(self):
        self.four_k: dict[int, dict] = {}
        self.deleted: list[int] = []
        self.searched: list[int] = []

    def call(self, service, method, path, **kwargs):
        ok, payload = self._route(service, method, path, kwargs)
        return servarr.Result(ok, payload, "ok" if ok else "rejected")

    def request(self, service, method, path, **kwargs):
        return self._route(service, method, path, kwargs)

    def _route(self, service, method, path, kwargs):
        params = kwargs.get("params") or {}
        if service == "radarr" and path == "/api/v3/movie":
            return True, LIBRARY
        if service != "radarr4k":
            return False, "unexpected"
        if method == "GET" and path == "/api/v3/movie":
            return True, [m for m in self.four_k.values() if m["tmdbId"] == params.get("tmdbId")]
        if path == "/api/v3/movie/lookup":
            tmdb = int(params["term"].split(":")[1])
            return True, [{"tmdbId": tmdb, "title": "x"}]
        if method == "POST" and path == "/api/v3/movie":
            movie_id = len(self.four_k) + 10
            self.four_k[movie_id] = {**kwargs["json"], "id": movie_id, "hasFile": False}
            return True, self.four_k[movie_id]
        if method == "GET" and path.startswith("/api/v3/movie/"):
            return True, self.four_k[int(path.rsplit("/", 1)[1])]
        if method == "DELETE":
            movie_id = int(path.rsplit("/", 1)[1])
            self.deleted.append(movie_id)
            self.four_k.pop(movie_id, None)
            return True, ""
        if path == "/api/v3/command":
            self.searched += kwargs["json"]["movieIds"]
            return True, {}
        if path == "/api/v3/queue":
            return True, {"records": []}
        return False, f"unhandled {method} {path}"


class TempFourKTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.arr = FakeArr()
        patches = [
            mock.patch.object(temp_4k, "_STATE_FILE", Path(self.dir.name) / "temp_4k.json"),
            mock.patch.object(temp_4k, "configured", return_value=True),
            mock.patch.object(servarr, "call", self.arr.call),
            mock.patch.object(servarr, "request", self.arr.request),
            mock.patch.object(servarr, "resolve_quality", return_value=(7, "Temp 4K")),
            mock.patch.object(servarr, "resolve_root_folder", return_value=("/temp-4k-movies", "")),
            mock.patch.dict("os.environ", {"TEMP_4K_HOURS": "48", "TEMP_4K_GIVE_UP_HOURS": "72"}),
            mock.patch.object(plex, "is_configured", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.dir.cleanup)
        self.alerts: list[tuple] = []

    def alert(self, kind, title, detail, **kw):
        self.alerts.append((kind, title))

    def test_request_adds_owned_film_to_4k_radarr(self):
        reply = temp_4k.request("Harry Potter and the Sorcerer's Stone")
        self.assertIn("Searching for a 4K copy", reply)
        (added,) = self.arr.four_k.values()
        self.assertEqual(added["qualityProfileId"], 7)
        self.assertEqual(added["rootFolderPath"], "/temp-4k-movies")
        self.assertEqual(temp_4k.entries()[0]["tmdb_id"], 671)

    def test_ambiguous_title_asks(self):
        reply = temp_4k.request("Harry Potter")
        self.assertIn("Several films match", reply)
        self.assertEqual(self.arr.four_k, {})

    def test_film_not_owned_is_refused(self):
        self.assertIn("can't find", temp_4k.request("Dune"))

    def test_already_4k_needs_nothing(self):
        self.assertIn("already 4K", temp_4k.request("The Dark Knight"))
        self.assertEqual(self.arr.four_k, {})

    def test_4k_remux_gets_a_lighter_copy(self):
        reply = temp_4k.request("Interstellar")
        self.assertIn("lighter 4K copy", reply)
        (added,) = self.arr.four_k.values()
        self.assertEqual(added["rootFolderPath"], "/temp-4k-movies")
        self.assertEqual(temp_4k.entries()[0]["tmdb_id"], 157336)

    def test_app_listing_extend_and_remove(self):
        temp_4k.request("Chamber of Secrets", hours=24)
        (film,) = temp_4k.listing()
        self.assertEqual((film["tmdb_id"], film["state"], film["expires_at"]), (672, "searching", None))
        self.assertEqual(temp_4k.extend(672, 24)["hours"], 48)  # pending: a longer keep once it lands
        movie_id = temp_4k.entries()[0]["radarr4k_id"]
        self.arr.four_k[movie_id]["hasFile"] = True
        temp_4k.tick(self.alert, now=1_000)
        with mock.patch.object(temp_4k.time, "time", return_value=1_000):
            film = temp_4k.extend(672, 24)
        self.assertEqual(film["state"], "ready")
        self.assertEqual(film["expires_at"], 1_000 + 48 * 3600 + 24 * 3600)
        with self.assertRaises(KeyError):
            temp_4k.extend(999)
        self.assertEqual(temp_4k.remove_id(672), (True, ""))
        self.assertEqual(self.arr.deleted, [movie_id])
        self.assertEqual(temp_4k.listing(), [])

    def test_ready_then_expired_is_deleted(self):
        temp_4k.request("Chamber of Secrets", hours=24)
        movie_id = temp_4k.entries()[0]["radarr4k_id"]
        temp_4k.tick(self.alert, now=1000.0)
        self.assertEqual(self.alerts, [])  # still downloading
        self.arr.four_k[movie_id]["hasFile"] = True
        temp_4k.tick(self.alert, now=2000.0)
        self.assertEqual(self.alerts[-1][0], "temp_4k_ready")
        self.assertEqual(temp_4k.entries()[0]["expires_at"], 2000.0 + 24 * 3600)
        temp_4k.tick(self.alert, now=2000.0 + 23 * 3600)
        self.assertEqual(self.arr.deleted, [])
        temp_4k.tick(self.alert, now=2000.0 + 24 * 3600)
        self.assertEqual(self.arr.deleted, [movie_id])
        self.assertEqual(temp_4k.entries(), [])
        self.assertEqual(self.alerts[-1][0], "temp_4k_removed")

    def test_asking_again_extends_instead_of_duplicating(self):
        temp_4k.request("Chamber of Secrets")
        movie_id = temp_4k.entries()[0]["radarr4k_id"]
        self.arr.four_k[movie_id]["hasFile"] = True
        temp_4k.tick(self.alert, now=5000.0)
        reply = temp_4k.request("Chamber of Secrets", hours=96)
        self.assertIn("already here in 4K", reply)
        self.assertEqual(len(self.arr.four_k), 1)
        self.assertGreater(temp_4k.entries()[0]["expires_at"], 5000.0 + 48 * 3600)

    def test_gives_up_when_no_release_appears(self):
        temp_4k.request("Chamber of Secrets")
        requested = temp_4k.entries()[0]["requested_at"]
        temp_4k.tick(self.alert, now=requested + 73 * 3600)
        self.assertEqual(self.alerts[-1][0], "temp_4k_failed")
        self.assertEqual(temp_4k.entries(), [])

    def test_remove_early(self):
        temp_4k.request("Sorcerer's Stone")
        self.assertIn("Removed the 4K copy", temp_4k.remove("sorcerer's stone"))
        self.assertEqual(temp_4k.entries(), [])

    def _ready(self, title="Chamber of Secrets", hours=24):
        temp_4k.request(title, hours=hours)
        movie_id = temp_4k.entries()[0]["radarr4k_id"]
        self.arr.four_k[movie_id]["hasFile"] = True
        temp_4k.tick(self.alert, now=2000.0)
        return movie_id

    def test_film_already_in_4k_radarr_is_adopted_and_never_deleted(self):
        self.arr.four_k[50] = {"id": 50, "tmdbId": 672, "hasFile": True}  # there before JARVIS
        temp_4k.request("Chamber of Secrets", hours=24)
        self.assertTrue(temp_4k.entries()[0]["adopted"])
        temp_4k.tick(self.alert, now=2000.0)
        temp_4k.tick(self.alert, now=2000.0 + 25 * 3600)
        self.assertEqual(self.arr.deleted, [])  # files left alone
        self.assertEqual(temp_4k.entries(), [])  # only the tracking goes
        self.assertIn(50, self.arr.four_k)

    def test_old_entries_without_the_field_still_expire(self):
        movie_id = self._ready()
        with temp_4k._lock:
            state = temp_4k._load()
            for entry in state:
                entry.pop("adopted", None)  # written before this field existed
            temp_4k._save(state)
        temp_4k.tick(self.alert, now=2000.0 + 24 * 3600)
        self.assertEqual(self.arr.deleted, [movie_id])

    def test_expiry_waits_while_the_film_is_playing(self):
        movie_id = self._ready()
        playing = [{"title": "Harry Potter and the Chamber of Secrets", "year": 2002, "guids": ["tmdb://672"]}]
        with mock.patch.object(plex, "is_configured", return_value=True), \
                mock.patch.object(plex, "playing_movies", return_value=(True, playing)):
            temp_4k.tick(self.alert, now=2000.0 + 24 * 3600)
            self.assertEqual(self.arr.deleted, [])
        with mock.patch.object(plex, "is_configured", return_value=True), \
                mock.patch.object(plex, "playing_movies", return_value=(True, [])):
            temp_4k.tick(self.alert, now=2000.0 + 26 * 3600)
        self.assertEqual(self.arr.deleted, [movie_id])

    def test_plex_errors_defer_only_for_a_bounded_time(self):
        movie_id = self._ready()
        with mock.patch.object(plex, "is_configured", return_value=True), \
                mock.patch.object(plex, "playing_movies", return_value=(False, "can't reach Plex")):
            temp_4k.tick(self.alert, now=2000.0 + 24 * 3600)
            self.assertEqual(self.arr.deleted, [])
            temp_4k.tick(self.alert, now=2000.0 + 24 * 3600 + temp_4k._PLEX_DEFER_MAX_S + 1)
        self.assertEqual(self.arr.deleted, [movie_id])

    def test_entry_removed_mid_pass_does_not_break_the_pass(self):
        temp_4k.request("Chamber of Secrets")
        movie_id = temp_4k.entries()[0]["radarr4k_id"]
        self.arr.four_k[movie_id]["hasFile"] = True
        real_request = self.arr.request

        def vanish(service, method, path, **kwargs):
            if path == f"/api/v3/movie/{movie_id}":
                with temp_4k._lock:
                    temp_4k._save([])  # Nick removed it just now
            return real_request(service, method, path, **kwargs)

        with mock.patch.object(servarr, "request", vanish):
            temp_4k.tick(self.alert, now=2000.0)
        self.assertEqual(self.alerts, [])
        self.assertEqual(temp_4k.entries(), [])

    def test_unconfigured_explains(self):
        with mock.patch.object(temp_4k, "configured", return_value=False):
            self.assertIn("isn't set up yet", temp_4k.request("Chamber of Secrets"))


if __name__ == "__main__":
    unittest.main()
