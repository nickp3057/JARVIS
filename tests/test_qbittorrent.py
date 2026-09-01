"""Tests for qBittorrent live download visibility."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import requests

from integrations import qbittorrent as qbit
from brain.tools import execute_tool, get_tool_specs


class QBittorrentTests(unittest.TestCase):
    def setUp(self) -> None:
        qbit._session_cache = None

    def tearDown(self) -> None:
        qbit._session_cache = None

    def test_login_uses_same_origin_headers_and_caches_session(self) -> None:
        session = Mock()
        session.headers = {}
        session.post.return_value = Mock(status_code=200, text="Ok.")
        env = {
            "QBITTORRENT_URL": "http://qbit.local:8080/",
            "QBITTORRENT_USERNAME": "jarvis",
            "QBITTORRENT_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            qbit.requests, "Session", return_value=session,
        ):
            first, error = qbit._session()
            second, _ = qbit._session()
        self.assertEqual(error, "")
        self.assertIs(first, session)
        self.assertIs(second, session)
        self.assertEqual(session.headers["Origin"], "http://qbit.local:8080")
        session.post.assert_called_once()

    def test_download_status_reports_progress_speed_and_eta(self) -> None:
        torrents = [{
            "name": "Example Movie",
            "progress": 0.625,
            "dlspeed": 2 * 1024 * 1024,
            "eta": 5400,
            "state": "downloading",
        }]
        transfer = {
            "dl_info_speed": 2 * 1024 * 1024,
            "up_info_speed": 256 * 1024,
            "connection_status": "connected",
        }
        with patch.object(qbit, "torrent_info", return_value=(True, torrents)), patch.object(
            qbit, "transfer_info", return_value=(True, transfer),
        ):
            result = qbit.download_status()
        self.assertIn("Example Movie: 62.5%", result)
        self.assertIn("1 hour 30 minutes", result)
        self.assertIn("2.0 MB/s down", result)

    def test_qbittorrent_52_accepts_204_login(self) -> None:
        session = Mock(headers={})
        session.post.return_value = Mock(status_code=204, text="")
        env = {
            "QBITTORRENT_URL": "http://qbit:8080",
            "QBITTORRENT_USERNAME": "jarvis",
            "QBITTORRENT_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            qbit.requests, "Session", return_value=session,
        ):
            result, error = qbit._session()
        self.assertIs(result, session)
        self.assertEqual(error, "")

    def test_download_snapshot_preserves_completed_items_for_monitor(self) -> None:
        torrents = [
            {"hash": "one", "name": "Done", "progress": 1, "state": "uploading", "eta": 0},
            {"hash": "two", "name": "Going", "progress": 0.5, "state": "downloading", "eta": 60},
        ]
        with patch.object(qbit, "torrent_info", return_value=(True, torrents)):
            ok, result = qbit.download_snapshot()
        self.assertTrue(ok)
        self.assertEqual(len(result), 2)

    def test_missing_configuration_fails_without_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(qbit.requests, "Session") as session:
            result, error = qbit._session()
        self.assertIsNone(result)
        self.assertIn("not configured", error)
        session.assert_not_called()

    def test_login_rejects_bad_credentials(self) -> None:
        session = Mock(headers={})
        session.post.return_value = Mock(status_code=200, text="Fails.")
        env = {
            "QBITTORRENT_URL": "http://qbit:8080",
            "QBITTORRENT_USERNAME": "bad",
            "QBITTORRENT_PASSWORD": "bad",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            qbit.requests, "Session", return_value=session,
        ):
            result, error = qbit._session()
        self.assertIsNone(result)
        self.assertIn("login failed", error)

    def test_login_network_failure_is_spoken_friendly(self) -> None:
        session = Mock(headers={})
        session.post.side_effect = requests.ConnectionError("offline")
        env = {
            "QBITTORRENT_URL": "http://qbit:8080",
            "QBITTORRENT_USERNAME": "jarvis",
            "QBITTORRENT_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            qbit.requests, "Session", return_value=session,
        ):
            result, error = qbit._session()
        self.assertIsNone(result)
        self.assertIn("offline", error)

    def test_expired_session_reauthenticates_once(self) -> None:
        expired = Mock()
        expired.get.return_value = Mock(status_code=403, text="Forbidden")
        fresh_response = Mock(status_code=200, text="")
        fresh_response.json.return_value = {"connection_status": "connected"}
        fresh = Mock()
        fresh.get.return_value = fresh_response
        qbit._session_cache = (expired, "http://qbit:8080", "jarvis", "secret")
        with patch.object(qbit, "_session", side_effect=[(expired, ""), (fresh, "")]), patch.object(
            qbit, "_config", return_value=("http://qbit:8080", "jarvis", "secret"),
        ):
            ok, result = qbit._request("/transfer/info")
        self.assertTrue(ok)
        self.assertEqual(result["connection_status"], "connected")
        self.assertIsNone(qbit._session_cache)

    def test_repeated_403_stops_after_single_retry(self) -> None:
        forbidden = Mock(status_code=403, text="Forbidden")
        first = Mock()
        first.get.return_value = forbidden
        second = Mock()
        second.get.return_value = forbidden
        with patch.object(qbit, "_session", side_effect=[(first, ""), (second, "")]), patch.object(
            qbit, "_config", return_value=("http://qbit:8080", "jarvis", "secret"),
        ):
            ok, error = qbit._request("/transfer/info")
        self.assertFalse(ok)
        self.assertIn("403", error)

    def test_plain_text_api_response_is_supported(self) -> None:
        response = Mock(status_code=200, text="v5.1.0")
        response.json.side_effect = ValueError
        session = Mock()
        session.get.return_value = response
        with patch.object(qbit, "_session", return_value=(session, "")), patch.object(
            qbit, "_config", return_value=("http://qbit", "user", "pass"),
        ):
            ok, value = qbit._request("/app/version")
        self.assertTrue(ok)
        self.assertEqual(value, "v5.1.0")

    def test_default_status_excludes_completed_torrents(self) -> None:
        torrents = [
            {"name": "Done", "progress": 1, "state": "uploading"},
            {"name": "Going", "progress": 0.2, "state": "stalledDL", "eta": 8_640_000},
        ]
        with patch.object(qbit, "torrent_info", return_value=(True, torrents)), patch.object(
            qbit, "transfer_info", return_value=(False, "offline"),
        ):
            result = qbit.download_status()
        self.assertNotIn("Done:", result)
        self.assertIn("Going: 20.0%", result)
        self.assertIn("ETA unknown", result)

    def test_empty_and_failed_torrent_lists_are_handled(self) -> None:
        with patch.object(qbit, "torrent_info", return_value=(True, [])), patch.object(
            qbit, "transfer_info", return_value=(True, {}),
        ):
            self.assertIn("No downloading torrents", qbit.download_status())
        with patch.object(qbit, "torrent_info", return_value=(False, "offline")):
            self.assertEqual(qbit.download_status(), "offline")

    def test_unexpected_snapshot_payload_is_rejected(self) -> None:
        with patch.object(qbit, "torrent_info", return_value=(True, {"wrong": "shape"})):
            ok, error = qbit.download_snapshot()
        self.assertFalse(ok)
        self.assertIn("Unexpected", error)

    def test_duration_and_byte_format_boundaries(self) -> None:
        self.assertEqual(qbit._duration(59), "under a minute")
        self.assertEqual(qbit._duration(60), "1 minute")
        self.assertEqual(qbit._duration(172800), "2 days")
        self.assertEqual(qbit._duration("bad"), "ETA unknown")
        self.assertEqual(qbit._bytes(1024**3), "1.0 GB")
        self.assertEqual(qbit._bytes("bad"), "unknown")

    def test_inventory_exposes_exact_hashes_for_review(self) -> None:
        torrent_hash = "a" * 40
        with patch.object(qbit, "torrent_info", return_value=(True, [{
            "hash": torrent_hash, "name": "Ubuntu", "state": "pausedDL",
        }])):
            result = qbit.torrent_inventory()
        self.assertIn(torrent_hash, result)
        self.assertIn("Ubuntu", result)

    def test_torrent_operation_requires_confirmation_and_live_exact_hash(self) -> None:
        torrent_hash = "b" * 40
        self.assertIn("Confirmation required", qbit.manage_torrent("pause", torrent_hash))
        self.assertIn("40-character", qbit.manage_torrent("pause", "short", confirmed=True))
        with patch.object(qbit, "torrent_info", return_value=(True, [])), patch.object(
            qbit, "_post",
        ) as post:
            self.assertIn("not in the current", qbit.manage_torrent("pause", torrent_hash, confirmed=True))
        post.assert_not_called()

    def test_confirmed_torrent_controls_use_exact_endpoint_and_payload(self) -> None:
        torrent_hash = "c" * 40
        live = [{"hash": torrent_hash, "name": "Linux ISO"}]
        cases = (
            ("pause", "/torrents/pause", {}),
            ("resume", "/torrents/resume", {}),
            ("recheck", "/torrents/recheck", {}),
            ("priority_top", "/torrents/topPrio", {}),
            ("delete", "/torrents/delete", {"deleteFiles": "false"}),
        )
        for action, endpoint, extra in cases:
            with self.subTest(action=action), patch.object(
                qbit, "torrent_info", return_value=(True, live),
            ), patch.object(qbit, "_post", return_value=(True, "ok")) as post:
                result = qbit.manage_torrent(action, torrent_hash, confirmed=True)
            self.assertIn("accepted", result.lower())
            post.assert_called_once_with(endpoint, data={"hashes": torrent_hash, **extra})

    def test_delete_files_requires_separate_explicit_flag(self) -> None:
        torrent_hash = "d" * 40
        live = [{"hash": torrent_hash, "name": "Disposable"}]
        with patch.object(qbit, "torrent_info", return_value=(True, live)), patch.object(
            qbit, "_post", return_value=(True, "ok"),
        ) as post:
            result = qbit.manage_torrent(
                "delete", torrent_hash, confirmed=True, delete_files=True,
            )
        post.assert_called_once_with(
            "/torrents/delete", data={"hashes": torrent_hash, "deleteFiles": "true"},
        )
        self.assertIn("downloaded files", result)

    def test_torrent_tools_are_registered_and_gate_mutations(self) -> None:
        names = {item.get("name") for item in get_tool_specs()}
        self.assertTrue({"torrent_inventory", "manage_torrent"}.issubset(names))
        result = execute_tool("manage_torrent", {
            "action": "resume", "torrent_hash": "e" * 40, "confirmed": False,
        })
        self.assertIn("Confirmation required", result)


if __name__ == "__main__":
    unittest.main()
