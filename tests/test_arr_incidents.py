"""Replay tests for the arr incident push policy (brain/arr_incidents.py).

Each test drives the real AlertTracker + IncidentPolicy with one poll per
minute (the monitor's default interval) and the same status-fetch and
maintenance logic the runtime loop uses, with default settings."""
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from brain import arr_incidents, arr_stack
from integrations import nas_stack

T = 1_800_000_000.0  # arbitrary epoch; NAS and Optiplex clocks agree unless a test says otherwise
MIN = 60.0
BURST = {
    'qbittorrent-api': 'qBittorrent API unreachable, authentication failed, or invalid response',
    'radarr-api': 'Radarr API unreachable, authentication failed, or invalid health response',
    'sonarr-api': 'Sonarr API unreachable, authentication failed, or invalid health response',
    'prowlarr-health': 'Prowlarr health: All indexer proxies are unavailable due to failures',
}


def watchdog_status(now, acted_at=None, state='running', phase='recreate-dependents', updated_at=None):
    recovery = None
    if acted_at is not None:
        recovery = {'request_id': 'x', 'source': 'dht', 'state': state, 'phase': phase,
                    'started_at': int(acted_at), 'updated_at': int(updated_at or now)}
    return True, {'observed_at': int(now), 'recovery': recovery, 'errors': [],
                  'watchdog_actions': {'.last_action': int(acted_at or 0), '.rotate_cooldown': 0}}


class ReplayBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cleared = {k: v for k, v in os.environ.items() if not k.startswith('ARR_STACK_')}
        env = patch.dict(os.environ, cleared, clear=True)
        env.start()
        self.addCleanup(env.stop)
        root = Path(self.tmp.name)
        self.tracker = arr_stack.AlertTracker(root / 'alerts.json')
        self.policy = arr_incidents.IncidentPolicy(root / 'incidents.json')
        self.pushes = []

    def poll(self, now, nodes, issues=None, status=None):
        snapshot = {'issues': dict(issues or {}), 'dht_nodes': nodes, 'connection_status': 'connected'}
        # Mirrors runtime._arr_stack_monitor_loop: status only when something looks wrong.
        if not (snapshot['issues'] or self.tracker.has_unconfirmed() or nodes == 0):
            status = None
        snapshot['maintenance'] = nas_stack.recovery_active(status)
        pushes, _ = arr_incidents.run_cycle(self.tracker, self.policy, snapshot, status, now)
        self.pushes.extend((now, p) for p in pushes)
        return pushes


class WatchdogRecoveryReplayTests(ReplayBase):
    def replay_incident(self, persists_until_minute):
        """DHT zero at T, watchdog recovery at T+15, API-unreachable and
        Prowlarr proxy alerts from T+16, clean from ``persists_until_minute``."""
        acted = T + 15 * MIN
        for minute in range(0, persists_until_minute + 30):
            now = T + minute * MIN
            recovered = minute >= persists_until_minute
            status = watchdog_status(now, acted if minute >= 15 else None,
                                     state='success' if recovered else 'running',
                                     phase='complete' if recovered else 'recreate-dependents',
                                     updated_at=min(now, T + persists_until_minute * MIN))
            if minute <= 15:
                self.poll(now, 0, status=status)
            elif not recovered:
                self.poll(now, None, BURST, status)
            else:
                self.poll(now, 250, status=status)

    def test_burst_after_watchdog_recovery_yields_one_summary(self):
        self.replay_incident(persists_until_minute=20)
        self.assertEqual(len(self.pushes), 1, self.pushes)
        at, push = self.pushes[0]
        self.assertEqual(at, T + 35 * MIN)  # hold (20 min) ends, stack re-checked
        self.assertEqual(push.kind, 'summary')
        self.assertTrue(push.message.startswith('Recovered after watchdog: the DHT watchdog recreated gluetun'))
        self.assertIn('took 5 min', push.message)
        self.assertTrue(push.thread_id.startswith('arr-stack-'))
        self.assertEqual(push.collapse_id, push.thread_id)

    def test_failure_past_hold_alerts_once_then_waits_for_escalation(self):
        for minute in range(0, 16):
            self.poll(T + minute * MIN, 0, status=watchdog_status(T + minute * MIN, T + 15 * MIN if minute == 15 else None))
        failed = dict(state='failed', phase='verify', updated_at=T + 23 * MIN)
        for minute in range(16, 35 + 359):
            now = T + minute * MIN
            self.poll(now, None, BURST, watchdog_status(now, T + 15 * MIN, **failed))
        self.assertEqual(len(self.pushes), 1, self.pushes)
        at, push = self.pushes[0]
        self.assertEqual(at, T + 35 * MIN)
        self.assertEqual(push.kind, 'alert')
        self.assertFalse(push.passive)
        self.assertIn('still failing 20 min after the DHT watchdog recreated gluetun', push.message)
        self.assertIn('but it failed at verify', push.message)
        for detail in BURST.values():
            self.assertIn(detail, push.message)
        # Default escalation interval: 6h after that alert, one reminder, same thread.
        now = T + (35 + 360) * MIN
        [reminder] = self.poll(now, None, BURST, watchdog_status(now, T + 15 * MIN, **failed))
        self.assertIn('Still failing after 6h', reminder.message)
        self.assertEqual(reminder.collapse_id, push.collapse_id)

    def test_indexer_backoff_left_after_hold_still_gets_the_quiet_summary(self):
        indexers = {'indexer:1': 'Indexer Torrent9 is down (Prowlarr backed it off after failures; next retry 12:00)',
                    'indexer:2': 'Indexer EZTV is down (Prowlarr backed it off after failures; next retry 12:00)'}
        acted = T + 15 * MIN
        for minute in range(0, 16 + 359):
            now = T + minute * MIN
            status = watchdog_status(now, acted if minute >= 15 else None, state='success' if minute >= 20 else 'running',
                                     phase='complete' if minute >= 20 else 'recreate-dependents', updated_at=min(now, T + 20 * MIN))
            if minute <= 15:
                self.poll(now, 0, status=status)
            elif minute < 20:
                self.poll(now, None, dict(BURST, **indexers), status)
            else:
                self.poll(now, 250, indexers, status)  # backoff persists until re-saved in Prowlarr
        self.assertEqual(len(self.pushes), 1, self.pushes)
        at, push = self.pushes[0]
        self.assertEqual(at, T + 35 * MIN)
        self.assertEqual((push.kind, push.passive), ('summary', True))
        self.assertIn('took 5 min', push.message)
        self.assertIn('2 Prowlarr indexer(s) still backed off', push.message)
        self.assertNotIn('test', push.message.lower())
        # At the digest threshold (6h from first seen) they move to the digest, never an alert.
        for minute in range(16 + 359, 16 + 362):
            now = T + minute * MIN
            self.poll(now, 250, indexers, watchdog_status(now, acted, state='success', phase='complete', updated_at=T + 20 * MIN))
        self.assertEqual(set(self.policy.state['digest']), set(indexers))
        self.assertEqual({p.kind for _, p in self.pushes} - {'digest'}, {'summary'})

    def test_hold_end_alert_lists_stack_failures_but_not_indexer_backoff(self):
        indexer = {'indexer:3': 'Indexer 1337x is down (Prowlarr backed it off after failures; next retry 12:00)'}
        for minute in range(0, 16):
            self.poll(T + minute * MIN, 0, status=watchdog_status(T + minute * MIN, T + 15 * MIN if minute == 15 else None))
        for minute in range(16, 40):
            now = T + minute * MIN
            self.poll(now, 250, dict(indexer, **{'radarr-api': BURST['radarr-api']}), watchdog_status(now, T + 15 * MIN))
        [(_, push)] = self.pushes
        self.assertEqual(push.kind, 'alert')
        self.assertIn(BURST['radarr-api'], push.message)
        self.assertNotIn('1337x', push.message)

    def test_nas_unreachable_breaks_through_the_hold(self):
        everything = dict(BURST, **{'prowlarr-api': 'Prowlarr API unreachable or readiness check failed'})
        self.poll(T, 0, status=watchdog_status(T, T))
        for minute in range(1, 19):
            # Recovery started, then the NAS drops off the network entirely.
            self.poll(T + minute * MIN, None, everything, (False, 'NAS SSH unavailable or timed out; outcome unknown'))
        kinds = [p.kind for _, p in self.pushes]
        self.assertEqual(kinds, ['urgent'])
        self.assertEqual(self.pushes[0][0], T + 11 * MIN)  # down since minute 1, alert after 10 min
        self.assertIn('NAS unreachable', self.pushes[0][1].message)


class BlipTests(ReplayBase):
    def test_two_minute_blip_never_pushes(self):
        for minute in range(0, 2):
            self.poll(T + minute * MIN, 250, {'radarr-api': BURST['radarr-api']})
        for minute in range(2, 30):
            self.poll(T + minute * MIN, 250)
        self.assertEqual(self.pushes, [])

    def test_confirmed_but_short_failure_stays_silent_including_recovery(self):
        # Four minutes: passes the 3-poll confirmation, not the 4-minute floor.
        for minute in range(0, 4):
            self.poll(T + minute * MIN, 250, {'sonarr-api': BURST['sonarr-api']})
        for minute in range(4, 30):
            self.poll(T + minute * MIN, 250)
        self.assertEqual(self.pushes, [])

    def test_real_outage_without_watchdog_alerts_then_quiet_recovery(self):
        for minute in range(0, 10):
            self.poll(T + minute * MIN, 250, {'radarr-api': BURST['radarr-api']})
        for minute in range(10, 20):
            self.poll(T + minute * MIN, 250)
        (at_alert, alert), (at_ok, ok) = self.pushes
        self.assertEqual(at_alert, T + 4 * MIN)
        self.assertEqual((alert.kind, alert.passive), ('alert', False))
        self.assertEqual((ok.kind, ok.passive), ('recovered', True))
        self.assertEqual(ok.collapse_id, alert.collapse_id)  # replaces the alert on the phone
        # The next incident gets a new identity.
        for minute in range(30, 40):
            self.poll(T + minute * MIN, 250, {'sonarr-api': BURST['sonarr-api']})
        self.assertNotEqual(self.pushes[-1][1].thread_id, alert.thread_id)

    def test_dht_zero_waits_for_the_watchdog(self):
        for minute in range(0, 25):
            self.poll(T + minute * MIN, 0, status=watchdog_status(T + minute * MIN))
        self.assertEqual(self.pushes, [])
        self.poll(T + 25 * MIN, 0, status=watchdog_status(T + 25 * MIN))
        [(_, push)] = self.pushes
        self.assertIn('DHT has been zero', push.message)


class IndexerDigestTests(ReplayBase):
    def test_long_backoff_moves_to_one_daily_digest(self):
        start = time.mktime(datetime(2026, 9, 28, 3, 0).timetuple())
        issue = {'indexer:7': 'Indexer The Pirate Bay is down (Prowlarr backed it off after failures; next retry 04:00)'}
        for step in range(0, 12 * 34):  # 34 hours at 5-minute polls
            self.poll(start + step * 300, 250, issue)
        kinds = [(datetime.fromtimestamp(at).strftime('%d %H:%M'), p.kind) for at, p in self.pushes]
        self.assertEqual(kinds, [('28 03:10', 'alert'), ('28 09:00', 'digest'), ('29 09:00', 'digest')])
        digest = self.pushes[1][1]
        self.assertTrue(digest.passive)
        self.assertEqual(digest.collapse_id, arr_incidents.DIGEST_THREAD)
        self.assertIn('The Pirate Bay (down 6h)', digest.message)
        self.assertIn('re-saving in the Prowlarr UI', digest.message)
        self.assertNotIn('test', digest.message.lower())
        self.assertNotIn('Still unresolved', ' '.join(p.message for _, p in self.pushes))


class WatchdogActionTests(unittest.TestCase):
    def test_stamp_without_recovery_record_reads_as_qbit_restart_and_skew_cancels(self):
        # NAS clock 90 s ahead; the stamp is 5 minutes old by the NAS's clock.
        status = (True, {'observed_at': 10_090, 'recovery': None, 'watchdog_actions': {'.last_action': 9_790}})
        action = arr_incidents.watchdog_action(status, 10_000)
        self.assertEqual(action, arr_incidents.WatchdogAction(9_700, 'the DHT watchdog restarted qBittorrent', 'dht'))

    def test_unavailable_status_is_no_action(self):
        self.assertIsNone(arr_incidents.watchdog_action((False, 'down'), 10))
        self.assertIsNone(arr_incidents.watchdog_action(None, 10))


if __name__ == '__main__':
    unittest.main()
