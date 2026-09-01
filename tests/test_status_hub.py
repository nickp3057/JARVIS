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
            "/api/status/media",
            "/api/status/services",
            "/api/links",
            "/api/home/entities",
        ):
            self.assertEqual(self.client.get(path).status_code, 401, path)

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


if __name__ == "__main__":
    unittest.main()
