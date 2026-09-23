"""Tests for the read-only companion dashboard endpoints and status hub."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import app.status_hub as status_hub

_NO_SERVICES_ENV = {
    "JARVIS_API_TOKEN": "test-token",
    "HOME_ASSISTANT_URL": "",
    "HA_URL": "",
    "PLEX_URL": "",
    "SONARR_URL": "",
    "SONARR_API_KEY": "",
    "RADARR_URL": "",
    "RADARR_API_KEY": "",
    "QBITTORRENT_URL": "",
    "QBITTORRENT_USERNAME": "",
    "QBITTORRENT_PASSWORD": "",
    "HOME_DASHBOARD_ENTITIES": "",
}


class StatusHubEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.clear_cache()
        self.env_patch = patch.dict(os.environ, dict(_NO_SERVICES_ENV))
        self.env_patch.start()
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.client.close()
        self.env_patch.stop()
        status_hub.clear_cache()

    def test_all_dashboard_endpoints_require_auth(self) -> None:
        for path in (
            "/api/status/torrents",
            "/api/now",
            "/api/status/media",
            "/api/status/storage",
            "/api/status/services",
            "/api/links",
            "/api/home/entities",
        ):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_now_snapshot_has_timers_reminders_and_next_event(self) -> None:
        timer = type("Timer", (), {
            "id": "abc123", "message": "Tea", "alarming": False,
            "paused": False, "expires_at": 2_000.0,
            "remaining_seconds": lambda self: 90.0,
        })()
        reminder = type("Reminder", (), {
            "id": "r123", "text": "Call Mum", "due_at": 1_500.0,
        })()
        event = {
            "id": "event-1", "summary": "Dentist", "start": None,
            "all_day": False, "location": "Main Street",
        }
        with patch.object(status_hub, "active_timers", return_value=[timer]), \
             patch.object(status_hub, "active_reminders", return_value=[reminder]), \
             patch.object(status_hub, "calendar_configured", return_value=True), \
             patch.object(status_hub, "upcoming_events", return_value=[event]), \
             patch.object(status_hub.time, "time", return_value=1_600.0):
            body = self.client.get("/api/now", headers=self.headers).json()
        self.assertEqual(body["timers"][0]["state"], "running")
        self.assertEqual(body["timers"][0]["remaining_seconds"], 90)
        self.assertTrue(body["reminders"][0]["is_due"])
        self.assertEqual(body["next_event"]["title"], "Dentist")
        self.assertEqual(body["generated_at"], 1_600.0)
        self.assertEqual(body["academic_events"], [])

    def test_now_snapshot_includes_academic_countdowns(self) -> None:
        from datetime import datetime, timedelta
        start = datetime.now().astimezone() + timedelta(days=3)
        events = [{
            "id": "quiz-1", "summary": "Biology Quiz", "start": start,
            "all_day": True, "location": "",
        }]
        with patch.object(status_hub, "active_timers", return_value=[]), \
             patch.object(status_hub, "active_reminders", return_value=[]), \
             patch.object(status_hub, "calendar_configured", return_value=True), \
             patch.object(status_hub, "upcoming_events", return_value=events):
            body = self.client.get("/api/now", headers=self.headers).json()
        self.assertEqual(body["academic_events"][0]["title"], "Biology Quiz")
        self.assertEqual(body["academic_events"][0]["days_left"], 3)

    def test_media_storage_reports_only_configured_filesystem(self) -> None:
        usage = type("Usage", (), {"total": 1_000, "used": 750, "free": 250})()
        with patch.dict(os.environ, {"MEDIA_STORAGE_PATH": "/media/video", "MEDIA_STORAGE_LABEL": "Movies"}), \
             patch.object(status_hub.shutil, "disk_usage", return_value=usage) as disk_usage:
            body = self.client.get("/api/status/storage", headers=self.headers).json()
        disk_usage.assert_called_once_with("/media/video")
        self.assertEqual(body["label"], "Movies")
        self.assertEqual(body["free_bytes"], 250)
        self.assertEqual(body["used_fraction"], 0.75)

    def test_now_refresh_reuses_calendar_without_interactive_consent(self) -> None:
        with patch.object(status_hub, "active_timers", return_value=[]), \
             patch.object(status_hub, "active_reminders", return_value=[]), \
             patch.object(status_hub, "calendar_configured", return_value=True), \
             patch.object(status_hub, "upcoming_events", return_value=[]) as calendar:
            status_hub.now_status()
            status_hub.invalidate("now")
            status_hub.now_status()
        calendar.assert_called_once_with(max_results=2500, hours_ahead=1440, allow_interactive=False)

    def test_partial_calendar_fetch_is_cached_only_briefly(self) -> None:
        import integrations.google_calendar as google_calendar

        with patch.object(status_hub, "upcoming_events", return_value=[]) as calendar:
            google_calendar.last_fetch_partial = True
            try:
                status_hub._calendar_events()
            finally:
                google_calendar.last_fetch_partial = False
            with patch.object(status_hub.time, "monotonic", return_value=status_hub.time.monotonic() + 31):
                status_hub._calendar_events()
        self.assertEqual(calendar.call_count, 2)

    def test_media_storage_unconfigured_degrades_cleanly(self) -> None:
        with patch.dict(os.environ, {
            "MEDIA_STORAGE_PATH": "", "RADARR_ROOT_FOLDER": "", "SONARR_ROOT_FOLDER": "",
        }):
            body = self.client.get("/api/status/storage", headers=self.headers).json()
        self.assertIn("not configured", body["error"])
        self.assertEqual(body["free_bytes"], 0)

    _NAS_DISKS = [
        {"path": "/", "freeSpace": 100, "totalSpace": 1_000},
        {"path": "/config", "freeSpace": 100, "totalSpace": 1_000},
        {"path": "/data", "freeSpace": 4_000, "totalSpace": 6_000},
        {"path": "/movies", "freeSpace": 4_000, "totalSpace": 6_000},
    ]

    def test_media_storage_falls_back_to_radarr_disk_space(self) -> None:
        """No local path: measure the NAS volume via Radarr, picking the mount
        that holds the root folder rather than the container's root disk."""
        with patch.dict(os.environ, {
            "MEDIA_STORAGE_PATH": "", "RADARR_ROOT_FOLDER": "", "SONARR_ROOT_FOLDER": "",
            "RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k",
        }), \
             patch.object(status_hub.shutil, "disk_usage") as disk_usage, \
             patch.object(status_hub, "servarr_request", return_value=(True, self._NAS_DISKS)) as req, \
             patch.object(status_hub, "get_root_folders", return_value=(True, [{"path": "/movies"}])):
            body = self.client.get("/api/status/storage", headers=self.headers).json()
        disk_usage.assert_not_called()
        req.assert_called_once_with("radarr", "GET", "/api/v3/diskspace")
        self.assertEqual(body["error"], "")
        self.assertEqual(body["total_bytes"], 6_000)
        self.assertEqual(body["free_bytes"], 4_000)
        self.assertEqual(body["used_bytes"], 2_000)
        self.assertEqual(body["used_fraction"], 0.3333)

    def test_media_storage_root_folder_setting_selects_mount(self) -> None:
        with patch.dict(os.environ, {
            "MEDIA_STORAGE_PATH": "", "RADARR_ROOT_FOLDER": "/data/media/movies",
            "RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k",
        }), \
             patch.object(status_hub, "servarr_request", return_value=(True, self._NAS_DISKS)), \
             patch.object(status_hub, "get_root_folders") as root_folders:
            body = self.client.get("/api/status/storage", headers=self.headers).json()
        root_folders.assert_not_called()
        self.assertEqual(body["total_bytes"], 6_000)

    def test_media_storage_local_path_takes_precedence_over_radarr(self) -> None:
        usage = type("Usage", (), {"total": 1_000, "used": 750, "free": 250})()
        with patch.dict(os.environ, {
            "MEDIA_STORAGE_PATH": "/mnt/nas", "RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k",
        }), \
             patch.object(status_hub.shutil, "disk_usage", return_value=usage), \
             patch.object(status_hub, "servarr_request") as req:
            body = self.client.get("/api/status/storage", headers=self.headers).json()
        req.assert_not_called()
        self.assertEqual(body["used_bytes"], 750)

    def test_media_storage_radarr_unreachable_degrades_with_error(self) -> None:
        with patch.dict(os.environ, {
            "MEDIA_STORAGE_PATH": "", "RADARR_URL": "http://nas:7878", "RADARR_API_KEY": "k",
        }), \
             patch.object(status_hub, "servarr_request", return_value=(False, "Could not reach Radarr")):
            body = self.client.get("/api/status/storage", headers=self.headers).json()
        self.assertIn("could not be read from Radarr", body["error"])
        self.assertEqual(body["total_bytes"], 0)

    def test_torrents_degrade_to_200_with_error_when_unreachable(self) -> None:
        with patch.object(status_hub, "list_transfers", return_value=(False, "down")):
            response = self.client.get("/api/status/torrents", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["error"], "down")
        self.assertEqual(body["torrents"], [])

    def test_torrents_success_shape(self) -> None:
        payload = {
            "dl_speed": 1000,
            "up_speed": 10,
            "torrents": [
                {"name": "A", "progress": 0.5, "state": "downloading",
                 "dl_speed": 1000, "up_speed": 0, "eta": 60, "size": 2048},
            ],
        }
        with patch.object(status_hub, "list_transfers", return_value=(True, payload)):
            body = self.client.get("/api/status/torrents", headers=self.headers).json()
        self.assertEqual(body["error"], "")
        self.assertEqual(body["dl_speed"], 1000)
        self.assertEqual(body["torrents"][0]["name"], "A")

    def test_torrents_are_cached_within_ttl(self) -> None:
        with patch.object(
            status_hub, "list_transfers",
            return_value=(True, {"dl_speed": 0, "up_speed": 0, "torrents": []}),
        ) as fetch:
            self.client.get("/api/status/torrents", headers=self.headers)
            self.client.get("/api/status/torrents", headers=self.headers)
        self.assertEqual(fetch.call_count, 1)

    def test_media_unconfigured_degrades_per_service(self) -> None:
        body = self.client.get("/api/status/media", headers=self.headers).json()
        for service in ("radarr", "sonarr"):
            self.assertIn("not configured", body[service]["error"])
            self.assertEqual(body[service]["queue"], [])
            self.assertEqual(body[service]["recent"], [])

    def test_media_queue_and_recent_shape(self) -> None:
        queue = {
            "records": [
                {"movie": {"title": "Dune"}, "size": 100, "sizeleft": 25,
                 "timeleft": "00:10:00", "trackedDownloadStatus": "ok"},
            ]
        }
        library = [
            {"title": f"Movie {i}", "year": 2000 + i, "added": f"2026-08-0{i}T00:00:00Z"}
            for i in range(1, 8)
        ]
        with patch.object(status_hub, "servarr_configured", return_value=True), \
             patch.object(status_hub, "get_queue", return_value=(True, queue)), \
             patch.object(status_hub, "servarr_request", return_value=(True, library)):
            body = self.client.get("/api/status/media", headers=self.headers).json()
        radarr = body["radarr"]
        self.assertEqual(radarr["error"], "")
        self.assertEqual(radarr["queue"][0]["title"], "Dune")
        self.assertEqual(radarr["queue"][0]["progress"], 0.75)
        self.assertEqual(len(radarr["recent"]), 5)
        self.assertEqual(radarr["recent"][0]["title"], "Movie 7")

    def test_services_report_unconfigured_as_down(self) -> None:
        body = self.client.get("/api/status/services", headers=self.headers).json()
        names = [entry["name"] for entry in body["services"]]
        self.assertEqual(
            names, ["Home Assistant", "Plex", "Sonarr", "Radarr", "qBittorrent"]
        )
        for entry in body["services"]:
            self.assertFalse(entry["up"])
            self.assertIn("not configured", entry["error"])
            self.assertIsNone(entry["latency_ms"])

    def test_links_omit_unset_and_strip_api_suffix(self) -> None:
        with patch.dict(os.environ, {
            "PLEX_URL": "http://nas:32400",
            "SONARR_URL": "http://nas:8989/api/v3",
            "SONARR_API_KEY": "key",
        }):
            body = self.client.get("/api/links", headers=self.headers).json()
        by_name = {entry["name"]: entry for entry in body["links"]}
        self.assertEqual(set(by_name), {"Plex", "Sonarr"})
        self.assertEqual(by_name["Sonarr"]["url"], "http://nas:8989")
        self.assertEqual(by_name["Plex"]["icon_hint"], "play")

    def test_home_entities_empty_allowlist_is_empty_not_error(self) -> None:
        body = self.client.get("/api/home/entities", headers=self.headers).json()
        self.assertEqual(body, {"error": "", "entities": []})

    def test_home_entities_filters_to_allowlist(self) -> None:
        states = [
            {"entity_id": "light.desk", "state": "on",
             "attributes": {"friendly_name": "Desk Lamp"}},
            {"entity_id": "light.other", "state": "off", "attributes": {}},
        ]
        with patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": "light.desk, light.missing"}), \
             patch.object(status_hub, "get_states", return_value=(True, states)):
            body = self.client.get("/api/home/entities", headers=self.headers).json()
        self.assertEqual(body["error"], "")
        self.assertEqual(
            body["entities"],
            [{"entity_id": "light.desk", "friendly_name": "Desk Lamp",
              "domain": "light", "state": "on"}],
        )

    def test_home_entities_degrade_when_ha_down(self) -> None:
        with patch.dict(os.environ, {"HOME_DASHBOARD_ENTITIES": "light.desk"}), \
             patch.object(status_hub, "get_states", return_value=(False, "no route")):
            body = self.client.get("/api/home/entities", headers=self.headers).json()
        self.assertEqual(body["error"], "no route")
        self.assertEqual(body["entities"], [])


class QuickActionEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.clear_cache()
        env = dict(_NO_SERVICES_ENV)
        env["HOME_DASHBOARD_ENTITIES"] = "light.desk,switch.lamp"
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start()
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.client.close()
        self.env_patch.stop()
        status_hub.clear_cache()

    # --- /api/home/control -------------------------------------------------

    def test_home_control_requires_auth(self) -> None:
        response = self.client.post(
            "/api/home/control", json={"entity_id": "light.desk", "action": "on"}
        )
        self.assertEqual(response.status_code, 401)

    def test_home_control_rejects_non_allowlisted_entity(self) -> None:
        with patch.object(status_hub, "call_discovered_service") as call:
            response = self.client.post(
                "/api/home/control",
                headers=self.headers,
                json={"entity_id": "lock.front_door", "action": "on"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("allowlist", response.json()["detail"])
        call.assert_not_called()

    def test_home_control_rejects_unsupported_action(self) -> None:
        response = self.client.post(
            "/api/home/control",
            headers=self.headers,
            json={"entity_id": "light.desk", "action": "toggle"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("on", response.json()["detail"])

    def test_home_control_success_returns_state_and_invalidates_cache(self) -> None:
        states = [{"entity_id": "light.desk", "state": "on",
                   "attributes": {"friendly_name": "Desk"}}]
        with patch.object(status_hub, "get_states", return_value=(True, states)) as get:
            # Prime the entities cache, then control, then read again: the
            # cache must be dropped so the second read refetches.
            self.client.get("/api/home/entities", headers=self.headers)
            with patch.object(
                status_hub, "call_discovered_service",
                return_value="Turn On command sent to light.desk.",
            ) as call:
                response = self.client.post(
                    "/api/home/control",
                    headers=self.headers,
                    json={"entity_id": "light.desk", "action": "on"},
                )
            self.client.get("/api/home/entities", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["error"], "")
        self.assertEqual(body["state"], "on")
        call.assert_called_once_with("light", "turn_on", entity_id="light.desk")
        # entities prime + control readback + entities refetch after invalidation
        self.assertEqual(get.call_count, 3)

    def test_home_control_service_failure_degrades_to_error_field(self) -> None:
        with patch.object(
            status_hub, "call_discovered_service",
            return_value="Command failed. Could not reach Home Assistant.",
        ):
            response = self.client.post(
                "/api/home/control",
                headers=self.headers,
                json={"entity_id": "switch.lamp", "action": "off"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("Command failed", body["error"])
        self.assertIsNone(body["state"])

    # --- /api/torrents/control ---------------------------------------------

    def test_torrent_control_requires_auth(self) -> None:
        response = self.client.post(
            "/api/torrents/control",
            json={"torrent_hash": "a" * 40, "action": "pause", "confirmed": True},
        )
        self.assertEqual(response.status_code, 401)

    def test_torrent_control_rejects_unsupported_action(self) -> None:
        for action in ("delete", "recheck", "priority_top"):
            response = self.client.post(
                "/api/torrents/control",
                headers=self.headers,
                json={"torrent_hash": "a" * 40, "action": action, "confirmed": True},
            )
            self.assertEqual(response.status_code, 400, action)

    def test_torrent_control_confirmation_gate_surfaces_as_error(self) -> None:
        with patch.object(
            status_hub, "manage_torrent",
            return_value=f"Confirmation required before pause on torrent {'a' * 40}.",
        ) as manage:
            response = self.client.post(
                "/api/torrents/control",
                headers=self.headers,
                json={"torrent_hash": "a" * 40, "action": "pause", "confirmed": False},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Confirmation required", response.json()["error"])
        manage.assert_called_once_with("pause", "a" * 40, confirmed=False)

    def test_torrent_control_success_invalidates_torrents_cache(self) -> None:
        empty = {"dl_speed": 0, "up_speed": 0, "torrents": []}
        with patch.object(
            status_hub, "list_transfers", return_value=(True, empty)
        ) as fetch:
            self.client.get("/api/status/torrents", headers=self.headers)
            with patch.object(
                status_hub, "manage_torrent",
                return_value="Pause accepted for Some Torrent.",
            ) as manage:
                response = self.client.post(
                    "/api/torrents/control",
                    headers=self.headers,
                    json={"torrent_hash": "a" * 40, "action": "pause", "confirmed": True},
                )
            self.client.get("/api/status/torrents", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], "")
        manage.assert_called_once_with("pause", "a" * 40, confirmed=True)
        self.assertEqual(fetch.call_count, 2)  # cache dropped after control

    def test_torrent_control_never_passes_delete_files(self) -> None:
        with patch.object(
            status_hub, "manage_torrent", return_value="Pause accepted for X.",
        ) as manage:
            self.client.post(
                "/api/torrents/control",
                headers=self.headers,
                json={"torrent_hash": "a" * 40, "action": "pause",
                      "confirmed": True, "delete_files": True},
            )
        self.assertNotIn("delete_files", manage.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()


class StatusHubEfficiencyTests(unittest.TestCase):
    def setUp(self) -> None:
        status_hub.clear_cache()
        self.addCleanup(status_hub.clear_cache)

    def test_concurrent_misses_compute_once(self):
        import threading
        calls = []
        gate = threading.Event()

        def slow():
            calls.append(1)
            gate.wait(1)
            return {"value": len(calls)}

        results = []
        threads = [threading.Thread(target=lambda: results.append(status_hub._cached("k", 5, slow))) for _ in range(4)]
        for thread in threads:
            thread.start()
        gate.set()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [{"value": 1}] * 4)

    def test_recent_library_is_fetched_less_often_than_queue(self):
        paths = []

        def fake_request(service, method, path, **_):
            paths.append((service, path))
            return True, [{"title": "A", "year": 2020, "added": "2026-01-01"}]

        with patch.object(status_hub, "servarr_configured", return_value=True), \
             patch.object(status_hub, "get_queue", return_value=(True, {"records": []})), \
             patch.object(status_hub, "servarr_request", side_effect=fake_request):
            status_hub._servarr_section("radarr")
            status_hub._servarr_section("radarr")
        self.assertEqual(paths, [("radarr", "/api/v3/movie")])

    def test_recent_library_failure_is_not_cached(self):
        with patch.object(status_hub, "servarr_configured", return_value=True), \
             patch.object(status_hub, "get_queue", return_value=(True, {"records": []})), \
             patch.object(status_hub, "servarr_request", side_effect=[(False, "down"), (True, [])]) as request:
            self.assertEqual(status_hub._servarr_section("sonarr")["error"], "down")
            self.assertEqual(status_hub._servarr_section("sonarr")["error"], "")
        self.assertEqual(request.call_count, 2)

    def test_service_pings_run_in_parallel_and_keep_order(self):
        import time as real_time

        def slow_get(url, **_):
            real_time.sleep(0.2)
            return None

        candidates = [(f"S{i}", f"http://s{i}", "x") for i in range(5)]
        with patch.object(status_hub, "_service_candidates", return_value=candidates), \
             patch.object(status_hub.requests, "get", side_effect=slow_get):
            started = real_time.monotonic()
            services = status_hub._services_uncached()["services"]
            elapsed = real_time.monotonic() - started
        self.assertLess(elapsed, 0.6)
        self.assertEqual([s["name"] for s in services], [c[0] for c in candidates])
