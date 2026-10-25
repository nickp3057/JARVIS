"""Media doctor: each pipeline stage stalls in plain English, with mocked arr APIs."""
import os
import time
import unittest
from unittest.mock import patch

from brain import confirm_gate
from integrations import media_doctor as doctor
from integrations import servarr_common as servarr

DUNE = {"id": 7, "title": "Dune", "year": 2021, "monitored": True, "hasFile": False, "isAvailable": True}
HASH = "A" * 40


class FakeArr:
    """Routes servarr.call/request by (service, method, path)."""

    def __init__(self, routes=None, down=()):
        self.routes = {("radarr", "GET", "/api/v3/system/status"): {"version": "5"},
                       ("sonarr", "GET", "/api/v3/system/status"): {"version": "4"},
                       ("sonarr", "GET", "/api/v3/series"): [],
                       ("radarr", "GET", "/api/v3/queue"): {"records": []},
                       ("sonarr", "GET", "/api/v3/queue"): {"records": []}}
        self.routes.update(routes or {})
        self.down = set(down)
        self.calls = []

    def call(self, service, method, path, **kwargs):
        self.calls.append((service, method, path, kwargs))
        if service in self.down:
            return servarr.Result(False, f"I can't reach {service.title()} at http://nas; the NAS or its container may be down.", "unreachable")
        if (service, method, path) in self.routes:
            return servarr.Result(True, self.routes[(service, method, path)], "ok")
        return servarr.Result(False, "no route", "not_found")

    def request(self, service, method, path, **kwargs):
        result = self.call(service, method, path, **kwargs)
        return result.ok, result.payload


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        confirm_gate.clear_for_tests()
        env = patch.dict(os.environ, {"RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k",
                                      "SONARR_URL": "http://nas:8989", "SONARR_API_KEY": "k",
                                      "PROWLARR_URL": "", "PLEX_URL": "", "PLEX_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)

    def run_doctor(self, arr, title="Dune", **patches):
        with patch.object(servarr, "call", arr.call), patch.object(servarr, "request", arr.request):
            ctx = [patch.object(*target, **kw) for target, kw in patches.values()]
            for c in ctx:
                c.start()
            try:
                return doctor.diagnose(title)
            finally:
                for c in ctx:
                    c.stop()

    def test_service_unreachable(self) -> None:
        report = self.run_doctor(FakeArr(down={"radarr", "sonarr"}))
        self.assertEqual(report["stage"], "radarr")
        self.assertIn("can't reach Radarr", report["summary"])
        self.assertEqual(report["fixes"][0]["id"], "restart_arr_stack")

    def test_not_requested(self) -> None:
        report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): []}))
        self.assertEqual(report["stage"], "request")
        self.assertIn("never added", report["summary"])

    def test_ambiguous_title_asks(self) -> None:
        both = [DUNE, dict(DUNE, id=8, year=1984)]
        report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): both}))
        self.assertEqual(report["stage"], "library")
        self.assertEqual(len(report["candidates"]), 2)
        narrowed = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): both}), title="Dune 2021")
        self.assertEqual(narrowed["item"]["id"], 7)

    def test_unmonitored(self) -> None:
        report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): [dict(DUNE, monitored=False)]}))
        self.assertEqual(report["stage"], "monitored")
        self.assertEqual(report["fixes"][0]["id"], "monitor_and_search")

    def test_not_released(self) -> None:
        movie = dict(DUNE, isAvailable=False, digitalRelease="2026-12-01")
        report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): [movie],
                                          ("radarr", "GET", "/api/v3/history/movie"): []}))
        self.assertEqual(report["stage"], "released")
        self.assertFalse(report["stalled"])

    def test_indexers_down(self) -> None:
        arr = FakeArr({("radarr", "GET", "/api/v3/movie"): [DUNE], ("radarr", "GET", "/api/v3/history/movie"): []})
        with patch.dict(os.environ, {"PROWLARR_URL": "http://nas:9696", "PROWLARR_API_KEY": "k"}):
            report = self.run_doctor(arr, outage=((doctor.nas_stack, "indexer_outages"),
                                                  {"return_value": {"indexer:1": "Indexer 1337x is down"}}))
        self.assertEqual(report["stage"], "indexer")
        self.assertIn("1337x", report["summary"])

    def test_no_release_found(self) -> None:
        report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): [DUNE],
                                          ("radarr", "GET", "/api/v3/history/movie"): []}))
        self.assertEqual(report["stage"], "search")
        self.assertEqual(report["fixes"][0]["id"], "search")

    def queued(self, torrent, **record):
        rec = {"id": 99, "movieId": 7, "downloadId": HASH, "protocol": "torrent",
               "title": "Dune.2021.720p", "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading",
               "sizeleft": 100}
        rec.update(record)
        arr = FakeArr({("radarr", "GET", "/api/v3/movie"): [DUNE],
                       ("radarr", "GET", "/api/v3/queue"): {"records": [rec]}})
        return self.run_doctor(arr, qbit=((doctor.qbittorrent, "torrent_by_hash"), {"return_value": (True, torrent)}))

    def test_stalled_torrent(self) -> None:
        now = time.time()
        report = self.queued({"state": "stalledDL", "progress": 0.42, "num_seeds": 0,
                              "last_activity": now - 7200})
        self.assertEqual(report["stage"], "download")
        self.assertTrue(report["stalled"])
        self.assertIn("42.0%", report["summary"])
        self.assertEqual(report["fixes"][0]["id"], "replace_release")

    def test_healthy_download(self) -> None:
        report = self.queued({"state": "downloading", "progress": 0.5, "dlspeed": 2_097_152, "eta": 600})
        self.assertFalse(report["stalled"])
        self.assertIn("2.0 MB/s", report["summary"])

    def test_dead_metadata(self) -> None:
        report = self.queued({"state": "metaDL", "progress": 0, "time_active": 7200})
        self.assertIn("metadata", report["summary"])

    def test_torrent_missing_from_client(self) -> None:
        report = self.queued(None)
        self.assertIn("doesn't have the torrent", report["summary"])

    def test_import_blocked(self) -> None:
        report = self.queued({}, trackedDownloadState="importBlocked", trackedDownloadStatus="warning", sizeleft=0,
                             statusMessages=[{"title": "x", "messages": ["No files found are eligible for import"]}])
        self.assertEqual(report["stage"], "import")
        self.assertIn("No files found are eligible", report["summary"])

    def test_imported_plex_skipped_without_token(self) -> None:
        movie = dict(DUNE, hasFile=True, movieFile={"relativePath": "Dune (2021).mkv"})
        report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): [movie]}))
        self.assertEqual(report["stage"], "imported")
        self.assertIn("Plex wasn't checked", report["summary"])

    def test_imported_but_not_on_plex(self) -> None:
        movie = dict(DUNE, hasFile=True)
        with patch.dict(os.environ, {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t"}):
            report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): [movie]}),
                                     plex=((doctor.plex, "find_title"), {"return_value": (True, [])}))
        self.assertEqual(report["stage"], "plex")
        self.assertEqual(report["fixes"][0]["id"], "refresh_plex")

    def test_done(self) -> None:
        movie = dict(DUNE, hasFile=True)
        with patch.dict(os.environ, {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t"}):
            report = self.run_doctor(FakeArr({("radarr", "GET", "/api/v3/movie"): [movie]}),
                                     plex=((doctor.plex, "find_title"), {"return_value": (True, [{"title": "Dune"}])}))
        self.assertEqual(report["stage"], "done")

    def test_sweep_reports_only_stuck_items(self) -> None:
        stuck = {"id": 1, "movie": {"title": "Dune"}, "trackedDownloadState": "importBlocked",
                 "trackedDownloadStatus": "warning", "sizeleft": 0, "statusMessages": []}
        arr = FakeArr({("radarr", "GET", "/api/v3/queue"): {"records": [stuck]}})
        report = self.run_doctor(arr, title="")
        self.assertEqual(report["stage"], "sweep")
        self.assertIn("Dune", report["summary"])

    def test_fix_requires_approval_and_runs_once(self) -> None:
        arr = FakeArr({("radarr", "GET", "/api/v3/movie"): [DUNE], ("radarr", "GET", "/api/v3/history/movie"): [],
                       ("radarr", "POST", "/api/v3/command"): {"id": 1}})
        report = self.run_doctor(arr)
        doctor.remember_fixes("s", report)
        with patch.object(servarr, "call", arr.call), patch.object(servarr, "request", arr.request):
            prompt = doctor.propose_fix("s", "fix1")
            self.assertIn("Nothing has been changed", prompt)
            self.assertFalse(any(c[1] == "POST" for c in arr.calls))
            self.assertIn("Search started", confirm_gate.conversation_action("yes", "s"))
        posts = [c for c in arr.calls if c[1] == "POST"]
        self.assertEqual(posts[0][3]["json"], {"name": "MoviesSearch", "movieIds": [7]})
        self.assertEqual(len(posts), 1)

    def test_restart_fix_uses_arr_restart_gate(self) -> None:
        doctor.remember_fixes("s", self.run_doctor(FakeArr(down={"radarr", "sonarr"})))
        with patch("brain.arr_stack.prepare_restart", return_value="restart prompt") as prepare:
            self.assertEqual(doctor.propose_fix("s", "fix1"), "restart prompt")
        prepare.assert_called_once_with("s")

    def test_fix_without_diagnosis_refused(self) -> None:
        self.assertIn("diagnose_media first", doctor.propose_fix("nobody", "fix1"))


if __name__ == "__main__":
    unittest.main()
