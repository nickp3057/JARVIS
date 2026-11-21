import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server

from brain import auto_heal, event_bus, self_repair, stack_fixes


class _InlineThread:
    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ran: list[str] = []
        fake_fixes = {name: stack_fixes.Fix(fix.label, (lambda n: lambda _: (self.ran.append(n) or (True, f'{n} ok')))(name))
                      for name, fix in stack_fixes.FIXES.items()}
        patches = [
            patch.object(self_repair, '_FILE', Path(self.tmp.name) / 'repairs.json'),
            patch.object(auto_heal, '_FILE', Path(self.tmp.name) / 'auto.json'),
            patch.object(self_repair, 'Thread', _InlineThread),
            patch.object(self_repair, '_notifier', None),
            patch.dict(stack_fixes.FIXES, fake_fixes),
            patch.dict(os.environ, {'AUTO_REPAIR_ENABLED': 'true', 'AUTO_REPAIR_FIXES': ''}),
            patch.object(auto_heal, '_plex_busy', return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        event_bus.reset_for_tests()

    def test_arr_failure_past_threshold_restarts_the_stack_on_its_own(self):
        now = time.time()
        started = auto_heal.consider_arr({'radarr-api': ('Radarr API unreachable', now - 900)}, maintenance=False)
        self.assertEqual(started, ['arr_recovery'])
        self.assertEqual(self.ran, ['arr_recovery'])
        repair = self_repair.public_proposals()[0]
        self.assertEqual(repair['approved_by'], 'auto')
        self.assertEqual(repair['link'], 'arr_stack')
        self.assertEqual(repair['status'], 'running')  # started; the follow-up decides
        alert = event_bus.publish('arr_stack', 'Jarvis — arr stack', 'Radarr down', source='arr-stack', push=False)
        self_repair.finish_arr_recovery('Arr stack recovered, sir. NAS verified gluetun healthy with DHT 300')
        self.assertEqual(self_repair.public_proposals()[0]['status'], 'applied')
        self.assertTrue(event_bus.is_cleared(alert))

    def test_arr_follow_up_failure_marks_the_repair_failed(self):
        auto_heal.consider_arr({'radarr-api': ('down', time.time() - 900)}, maintenance=False)
        self_repair.finish_arr_recovery('Arr recovery failed at verify (exit 1), sir.')
        self.assertEqual(self_repair.public_proposals()[0]['status'], 'failed')

    def test_young_failures_and_maintenance_wait(self):
        now = time.time()
        self.assertEqual(auto_heal.consider_arr({'radarr-api': ('down', now - 60)}, maintenance=False), [])
        self.assertEqual(auto_heal.consider_arr({'radarr-api': ('down', now - 900)}, maintenance=True), [])
        self.assertEqual(auto_heal.consider_arr({'radarr-api': ('down', now - 900)}, maintenance=False,
                                                hold_until=now + 60), [])
        # The DHT watchdog gets its turn first (25 min + 5).
        self.assertEqual(auto_heal.consider_arr({'dht-zero': ('DHT 0', now - 900)}, maintenance=False), [])
        self.assertEqual(self.ran, [])

    def test_cooldown_stops_a_restart_loop(self):
        now = time.time()
        failing = {'sonarr-api': ('down', now - 900)}
        auto_heal.consider_arr(failing, maintenance=False)
        auto_heal.consider_arr(failing, maintenance=False)
        self.assertEqual(self.ran, ['arr_recovery'])
        self.assertIn('already ran', auto_heal.blocked_reason('arr_recovery'))

    def test_plex_is_never_automatic(self):
        with patch.dict(os.environ, {'AUTO_REPAIR_FIXES': 'refresh_plex,arr_recovery'}):
            self.assertNotIn('refresh_plex', auto_heal.allowed_fixes())
            self.assertFalse(auto_heal.permitted('refresh_plex'))
            reply = self_repair.propose_stack_fix('refresh_plex', 'test', None, auto=True)
        self.assertIn('Staged repair', reply)
        self.assertEqual(self.ran, [])
        pid = self_repair.public_proposals()[0]['id']
        self.assertIn("isn't one I may run", self_repair.apply(pid, confirmed=True, approved_by='auto'))

    def test_home_assistant_restart_waits_while_plex_plays(self):
        with patch.object(auto_heal, '_plex_busy', return_value=True):
            self.assertIn('Plex', auto_heal.blocked_reason('restart_home_assistant'))
        with patch.object(auto_heal, '_plex_busy', return_value=None):
            self.assertIn('Plex', auto_heal.blocked_reason('restart_home_assistant'))

    def test_switched_off_means_staged_for_approval(self):
        with patch.dict(os.environ, {'AUTO_REPAIR_ENABLED': 'false'}):
            now = time.time()
            self.assertEqual(auto_heal.consider_arr({'radarr-api': ('down', now - 900)}, maintenance=False), [])
            reply = self_repair.propose_stack_fix('retry_download', 'x', None, params={'service': 'radarr', 'queue_id': 1},
                                                  key='abc', auto=True)
        self.assertIn('Staged repair', reply)
        self.assertEqual(self.ran, [])

    def test_indexer_backoff_retests_then_tries_a_mirror(self):
        now = time.time()
        started = auto_heal.consider_arr({'indexer:7': ('Indexer 1337x is down (backed off)', now - 8000)},
                                         maintenance=False)
        self.assertEqual(started, ['retest_indexers', 'switch_indexer_mirror:7'])
        mirror = next(p for p in self_repair.public_proposals() if p['target'].startswith('switch_indexer_mirror'))
        self.assertIn('1337x', mirror['label'])

    def test_successful_retry_clears_its_alert_and_links_it(self):
        alert = event_bus.publish('download_stalled', 'Download stalled', 'Film: no peers', source='downloads',
                                  context={'torrent_hash': 'ABCDEF0123456789FFFF'}, push=False)
        link = auto_heal.link_for(alert.kind, alert.context)
        self.assertEqual(link, 'torrent:abcdef0123456789')
        self_repair.propose_stack_fix('retry_download', 'stalled', None, params={'service': 'radarr', 'queue_id': 1},
                                      key='abcdef0123456789', link=link, auto=True)
        self.assertEqual(self.ran, ['retry_download'])
        self.assertTrue(event_bus.is_cleared(alert))
        linked = self_repair.latest_for_link(link)
        self.assertTrue(linked['auto'])
        self.assertEqual(linked['status'], 'applied')

    def test_fix_it_runs_without_cooldown_and_says_so(self):
        alert = SimpleNamespace(id=1, kind='arr_stack', context={})
        with patch('integrations.nas_stack.poll_health', return_value={'issues': {'radarr-api': 'down'}}):
            result = auto_heal.fix_alert(alert)
            self.assertTrue(result['started'])
            again = auto_heal.fix_alert(alert)
        self.assertTrue(again['started'])  # Nick's tap isn't rate-limited
        self.assertEqual(self.ran, ['arr_recovery', 'arr_recovery'])

    def test_fix_it_on_healthy_stack_clears_the_alert(self):
        alert = event_bus.publish('arr_stack', 'Jarvis — arr stack', 'Radarr down', source='arr-stack', push=False)
        with patch('integrations.nas_stack.poll_health', return_value={'issues': {}}):
            result = auto_heal.fix_alert(alert)
        self.assertFalse(result['started'])
        self.assertTrue(result['resolved'])
        self.assertTrue(event_bus.is_cleared(alert))


class AlertEndpointTests(unittest.TestCase):
    def setUp(self):
        AutopilotTests.setUp(self)
        token = patch.dict(os.environ, {'JARVIS_API_TOKEN': 'test-token'})
        token.start()
        self.addCleanup(token.stop)
        server._runtime = MagicMock()
        self.addCleanup(setattr, server, '_runtime', None)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer test-token'}

    def test_recent_alerts_carry_their_repair_and_dismiss_hides_repeats(self):
        for _ in range(2):
            event_bus.publish('arr_stack', 'Jarvis — arr stack', 'Radarr down', source='arr-stack', push=False)
        auto_heal.consider_arr({'radarr-api': ('Radarr down', time.time() - 900)}, maintenance=False)
        [row] = self.client.get('/api/alerts/recent', headers=self.headers).json()['alerts']
        self.assertEqual(row['repeats'], 2)
        self.assertTrue(row['fixable'])
        self.assertTrue(row['repair']['auto'])
        self.assertNotIn('_link', row)
        body = self.client.post(f"/api/alerts/{row['id']}/dismiss", json={}, headers=self.headers).json()
        self.assertEqual(body['dismissed'], 2)
        self.assertEqual(self.client.get('/api/alerts/recent', headers=self.headers).json()['alerts'], [])

    def test_autopilot_status(self):
        body = self.client.get('/api/autopilot', headers=self.headers).json()
        self.assertTrue(body['enabled'])
        self.assertIn('refresh_plex', body['never'])
        self.assertNotIn('refresh_plex', body['fixes'])


if __name__ == '__main__':
    unittest.main()
