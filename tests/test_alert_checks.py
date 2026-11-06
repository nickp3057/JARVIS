import time
import unittest
from unittest.mock import patch

from brain import alert_checks, event_bus


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        alert_checks.battery_reports.clear()
        env = patch.dict("os.environ", {"ALERT_REPEAT_WINDOW_S": "0"})
        env.start()
        self.addCleanup(env.stop)

    def titles(self) -> list[str]:
        return [a.title for a in alert_checks.needs_attention()]

    def test_fixed_alerts_disappear_and_real_ones_stay(self) -> None:
        event_bus.publish("schwab_login", "Schwab needs you", "Log in.", source="schwab")
        event_bus.publish("download_stalled", "Download stalled", "Gone", source="downloads",
                          context={"torrent_hash": "a" * 40})
        event_bus.publish("download_stalled", "Download stalled", "Stuck", source="downloads",
                          context={"torrent_hash": "b" * 40})
        event_bus.publish("device_offline", "Lamp offline", "m", source="home",
                          context={"entity_id": "light.lamp"})
        event_bus.publish("device_battery", "iPad battery low", "m", source="device-battery",
                          context={"device": "iPad"})
        event_bus.publish("arr_stack", "Radarr down", "m", source="arr")
        event_bus.publish("arr_stack", "Radarr recovered", "m", source="arr", severity="info")
        event_bus.publish("repair", "Fix failed", "m", source="self-repair")  # no check: stays
        alert_checks.battery_reports["iPad"] = (0.55, True)
        stuck = {"hash": "b" * 40, "progress": 0.1, "state": "stalledDL"}
        with patch("integrations.nas_stack.poll_health", return_value={"issues": {}, "dht_nodes": 50}), \
                patch("integrations.schwab.linked", return_value=True), \
                patch("integrations.schwab.login_warning", return_value=None), \
                patch("integrations.qbittorrent.torrent_info", return_value=(True, [stuck])) as qbit, \
                patch("integrations.media_doctor.no_progress_stall", return_value="no data for 5 h"), \
                patch("integrations.home_assistant.get_states",
                      return_value=(True, [{"entity_id": "light.lamp", "state": "unavailable"}])):
            result = alert_checks.refresh()
        self.assertEqual(qbit.call_count, 1)  # one call for every download alert
        self.assertEqual(result, {"resolved": 4, "remaining": 3})
        self.assertEqual(sorted(self.titles()), ["Download stalled", "Fix failed", "Lamp offline"])

    def test_partial_nas_recovery_cannot_hide_critical_disk_fault(self) -> None:
        event_bus.publish("nas_host", "SMART failure", "Disk failed", source="nas-host", severity="critical",
                          context={"condition_keys": ["smart:sda"]})
        event_bus.publish("nas_host", "Plex unhealthy", "Plex down", source="nas-host",
                          context={"condition_keys": ["container:plex"]})
        event_bus.publish("nas_host", "NAS recovered", "Cleared: Plex down", source="nas-host", severity="info")
        now = time.time()
        status = (True, {"observed_at": now, "host_health": {
            "generated_at": now, "disks": [{"device": "sda", "smart_passed": False}],
            "containers": [{"name": "plex", "state": "running"}]}})
        with patch("integrations.nas_stack.watchdog_status", return_value=status) as probe:
            self.assertEqual(alert_checks.refresh(), {"resolved": 1, "remaining": 1})
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(self.titles(), ["SMART failure"])

    def test_missing_or_stale_nas_telemetry_is_not_recovery(self) -> None:
        event_bus.publish("nas_host", "SMART failure", "Disk failed", source="nas-host", severity="critical",
                          context={"condition_keys": ["smart:sda"]})
        now = time.time()
        for health in ({"generated_at": now, "disks": [{"device": "sda", "error": "probe failed"}]},
                       {"generated_at": now - 3600, "disks": [{"device": "sda", "smart_passed": True}]}):
            with self.subTest(health=health), patch("integrations.nas_stack.watchdog_status",
                    return_value=(True, {"observed_at": now, "host_health": health})):
                self.assertEqual(alert_checks.refresh(), {"resolved": 0, "remaining": 1})

    def test_unknown_and_legacy_alerts_survive_unrelated_info(self) -> None:
        event_bus.publish("nas_host", "SMART failure", "Disk failed", source="nas-host", severity="critical")
        event_bus.publish("nas_host", "NAS recovered", "Plex up", source="nas-host", severity="info")
        self.assertEqual(alert_checks.refresh(), {"resolved": 0, "remaining": 1})

    def test_partial_arr_recovery_keeps_warning(self) -> None:
        event_bus.publish("arr_stack", "Stack down", "Radarr and Sonarr down", source="arr-stack")
        event_bus.publish("arr_stack", "Recovered", "Radarr up; Sonarr still failing", source="arr-stack", severity="info")
        with patch("integrations.nas_stack.poll_health", return_value={"issues": {"sonarr-api": "down"}, "dht_nodes": 50}):
            self.assertEqual(alert_checks.refresh(), {"resolved": 0, "remaining": 1})

    def test_a_broken_check_keeps_the_alert(self) -> None:
        event_bus.publish("schwab_login", "Schwab needs you", "Log in.", source="schwab")
        with patch("integrations.schwab.linked", side_effect=OSError("disk")):
            self.assertEqual(alert_checks.refresh(), {"resolved": 0, "remaining": 1})
        self.assertEqual(self.titles(), ["Schwab needs you"])


if __name__ == "__main__":
    unittest.main()
