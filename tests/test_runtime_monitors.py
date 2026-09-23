"""Focused tests for runtime snapshot and proactive monitor transitions."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from brain.runtime import JarvisRuntime, _build_home_snapshot


class _TwoIterationStop:
    def __init__(self) -> None:
        self.waits = 0

    def is_set(self) -> bool:
        return False

    def wait(self, _: float) -> bool:
        self.waits += 1
        return self.waits >= 2


class RuntimeMonitorTests(unittest.TestCase):
    def test_home_snapshot_counts_states_by_domain(self) -> None:
        snapshot = _build_home_snapshot({
            "light": [
                {"state": "on"},
                {"state": "off"},
                {"state": "unavailable"},
            ],
            "media_player": [{"state": "playing"}, {"state": "idle"}],
        })
        self.assertTrue(snapshot.online)
        self.assertEqual(snapshot.entity_count, 5)
        self.assertEqual(snapshot.domains["light"].on_count, 1)
        self.assertEqual(snapshot.domains["light"].off_count, 1)
        self.assertEqual(snapshot.domains["light"].unavailable_count, 1)
        self.assertEqual(snapshot.domains["media_player"].active_count, 2)

    def test_attribute_only_home_events_skip_snapshot_rebuild(self) -> None:
        import threading
        runtime = object.__new__(JarvisRuntime)
        runtime._stop_monitors = threading.Event()
        runtime._home_monitor_failed = False
        runtime._home_event_last_seen = {}
        runtime._update_home_snapshot_from_entities = Mock()
        runtime._mark_home_monitor_healthy = Mock()
        runtime.record_event = Mock()
        light = {"entity_id": "light.desk", "state": "on", "attributes": {"brightness": 10}}

        def fake_listen(stop, changed, on_connected):
            on_connected()
            rebuilds = runtime._update_home_snapshot_from_entities.call_count
            changed({"entity_id": "light.desk", "old_state": light, "new_state": {**light, "attributes": {"brightness": 200}}})
            self.assertEqual(runtime._update_home_snapshot_from_entities.call_count, rebuilds)
            changed({"entity_id": "light.desk", "old_state": light, "new_state": {**light, "state": "off"}})
            self.assertEqual(runtime._update_home_snapshot_from_entities.call_count, rebuilds + 1)
            stop.set()

        with patch("brain.runtime.get_states", return_value=(True, [light])), \
             patch("brain.runtime.listen_state_changes", side_effect=fake_listen):
            runtime._home_websocket_monitor_loop()

    def test_home_change_filter_ignores_noise_and_deduplicates(self) -> None:
        runtime = object.__new__(JarvisRuntime)
        runtime._home_event_last_seen = {}
        self.assertFalse(runtime._should_record_home_change("light.office", "unknown", "on"))
        with patch.dict(os.environ, {"HOME_EVENT_COOLDOWN_S": "60"}, clear=False):
            self.assertTrue(runtime._should_record_home_change("light.office", "off", "on"))
            self.assertFalse(runtime._should_record_home_change("light.office", "off", "on"))

    def test_download_monitor_announces_only_new_completion_transition(self) -> None:
        runtime = object.__new__(JarvisRuntime)
        runtime._stop_monitors = _TwoIterationStop()
        runtime._download_monitor_failed = False
        runtime.record_event = Mock()
        runtime.announce = Mock()
        first = [{"hash": "abc", "name": "Movie", "progress": 0.9, "state": "downloading"}]
        second = [{"hash": "abc", "name": "Movie", "progress": 1.0, "state": "uploading"}]
        with patch(
            "integrations.qbittorrent.download_snapshot",
            side_effect=[(True, first), (True, second)],
        ), patch.dict(os.environ, {
            "QBITTORRENT_MONITOR_INTERVAL_S": "0",
            "QBITTORRENT_ANNOUNCE_COMPLETIONS": "true",
        }, clear=False):
            runtime._download_monitor_loop()
        runtime.announce.assert_called_once()
        self.assertIn("Movie", runtime.announce.call_args.args[0])

    def test_download_monitor_records_error_transition_without_speaking(self) -> None:
        runtime = object.__new__(JarvisRuntime)
        runtime._stop_monitors = _TwoIterationStop()
        runtime._download_monitor_failed = False
        runtime.record_event = Mock()
        runtime.announce = Mock()
        first = [{"hash": "abc", "name": "Movie", "progress": 0.5, "state": "downloading"}]
        second = [{"hash": "abc", "name": "Movie", "progress": 0.5, "state": "error"}]
        with patch(
            "integrations.qbittorrent.download_snapshot",
            side_effect=[(True, first), (True, second)],
        ), patch.dict(os.environ, {
            "QBITTORRENT_MONITOR_INTERVAL_S": "0",
            "QBITTORRENT_ANNOUNCE_COMPLETIONS": "false",
        }, clear=False):
            runtime._download_monitor_loop()
        alerts = [call for call in runtime.record_event.call_args_list if call.kwargs.get("kind") == "alert"]
        self.assertEqual(len(alerts), 1)
        runtime.announce.assert_not_called()

    def test_download_monitor_reports_one_outage_for_repeated_failures(self) -> None:
        runtime = object.__new__(JarvisRuntime)
        runtime._stop_monitors = _TwoIterationStop()
        runtime._download_monitor_failed = False
        runtime.record_event = Mock()
        runtime.announce = Mock()
        with patch(
            "integrations.qbittorrent.download_snapshot",
            side_effect=[(False, "offline"), (False, "still offline")],
        ), patch.dict(os.environ, {"QBITTORRENT_MONITOR_INTERVAL_S": "0"}, clear=False):
            runtime._download_monitor_loop()
        offline = [
            call for call in runtime.record_event.call_args_list
            if call.kwargs.get("title") == "Download monitor offline"
        ]
        self.assertEqual(len(offline), 1)


if __name__ == "__main__":
    unittest.main()
