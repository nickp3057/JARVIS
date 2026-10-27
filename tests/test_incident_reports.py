import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import brain.debug_reports as debug_reports
from brain import event_bus, incident_reports, self_repair

_ALL_DOWN = {k: f'{k} down' for k in ('qbittorrent-api', 'radarr-api', 'sonarr-api', 'prowlarr-api')}


def _status(now, **extra):
    data = {'observed_at': now, 'recovery': None, 'watchdog_actions': {}, 'log_tail': {}, 'errors': []}
    data.update(extra)
    return True, data


class SuspectedCauseTests(unittest.TestCase):
    def test_all_apis_down_with_nas_answering_blames_gluetun(self):
        causes = incident_reports.suspected_causes({'issues': _ALL_DOWN}, _status(1000), [], now=1000)
        self.assertIn('gluetun', causes[0])

    def test_all_apis_and_status_ssh_down_blames_the_nas(self):
        causes = incident_reports.suspected_causes({'issues': _ALL_DOWN}, (False, 'timed out'), [], now=1000)
        self.assertIn('NAS itself is unreachable', causes[0])

    def test_zero_dht_with_dns_timeouts_names_the_vpn_tunnel(self):
        tail = ['WARN [vpn] restarting VPN because it failed to pass the healthcheck: lookup cloudflare.com: i/o timeout']
        causes = incident_reports.suspected_causes({'issues': {}, 'dht_nodes': 0}, _status(1000), tail, now=1000)
        self.assertTrue(any('carries no traffic' in c for c in causes))

    def test_recent_failed_recovery_is_reported(self):
        recovery = {'source': 'dht', 'state': 'failed', 'phase': 'verify', 'updated_at': 400,
                    'gluetun_health': 'unhealthy', 'dht_nodes': 0}
        causes = incident_reports.suspected_causes({'issues': {}, 'dht_nodes': 0}, _status(1000, recovery=recovery), [],
                                                   now=1000)
        self.assertTrue(any('failed at verify' in c for c in causes))

    def test_running_recovery_comes_first(self):
        recovery = {'source': 'jarvis', 'state': 'running', 'phase': 'vpn-settle', 'updated_at': 990}
        causes = incident_reports.suspected_causes({'issues': _ALL_DOWN}, _status(1000, recovery=recovery), [], now=1000)
        self.assertIn('expected', causes[0])

    def test_indexer_backoff(self):
        issues = {'indexer:3': 'Indexer Foo is down (Prowlarr backed it off after failures; next retry 10:00)'}
        causes = incident_reports.suspected_causes({'issues': issues}, None, [], now=1000)
        self.assertIn('Prowlarr indexer', causes[0])

    def test_nothing_failing_says_so(self):
        causes = incident_reports.suspected_causes({'issues': {}, 'dht_nodes': 300}, None, [], now=1000)
        self.assertEqual(len(causes), 1)
        self.assertIn('cleared', causes[0])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(debug_reports, '_REPORTS_FILE', Path(self.tempdir.name) / 'debug_reports.json'),
            patch.object(self_repair, '_FILE', Path(self.tempdir.name) / 'repairs.json'),
        ]
        for p in self.patches:
            p.start()
        event_bus.reset_for_tests()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        event_bus.reset_for_tests()
        self.tempdir.cleanup()

    def _evidence(self, now):
        tail = ['[2026-10-25 18:20:02] DHT nodes at zero (consecutive check 76/3)']
        return {
            'health': {'issues': dict(_ALL_DOWN), 'dht_nodes': None, 'connection_status': 'unknown'},
            'status': _status(now, log_tail={'watchdog.log': tail}, watchdog_actions={'.last_action': now - 600}),
            'media': ['1 problem(s): Radarr is not answering'],
            'radarr_log': ['Radarr 2026-10-25T18:00:00 Error: Connection refused'],
            'sonarr_log': TimeoutError('timed out'),
            'prowlarr_log': [], 'qbit_log': [], 'ha_log': [],
        }

    def test_build_stores_an_incident_report_and_prefills_the_prompt(self):
        earlier = event_bus.publish('arr_stack', 'Radarr unreachable', 'first poll failed', source='arr-stack')
        alert = event_bus.publish('arr_stack', 'Jarvis — arr stack', 'Radarr, Sonarr unreachable', source='arr-stack')
        with patch.object(incident_reports, '_gather', return_value=self._evidence(time.time())):
            result = incident_reports.build(alert, session_id='s1')
        self.assertIn(result['report_id'], result['prompt'])
        self.assertIn('Suspected cause: ', result['prompt'])
        self.assertIn('gluetun', result['prompt'])
        text = debug_reports.describe_debug_report(result['report_id'])
        self.assertIn('Alert: Jarvis — arr stack', text)
        self.assertIn('DHT watchdog restarted qBittorrent 10 min ago', text)
        self.assertIn(f'Related alert', text)
        self.assertIn(earlier.title, text)
        self.assertIn('Connection refused', text)
        self.assertIn('unavailable (timed out)', text)
        stored = debug_reports.get_debug_report(result['report_id'])
        self.assertEqual(stored['source'], 'incident')
        self.assertEqual(stored['messages'], [])
        self.assertEqual(stored['app_context']['alert_id'], str(alert.id))

    def test_host_health_replaces_the_gap_note(self):
        alert = event_bus.publish('arr_stack', 'Jarvis — arr stack', 'qBittorrent unreachable', source='arr-stack')
        now = time.time()
        evidence = self._evidence(now)
        evidence['status'][1]['host_health'] = {
            'generated_at': now - 60, 'errors': [],
            'containers': [{'name': 'gluetun', 'state': 'running', 'health': 'unhealthy', 'restart_count': 4}],
            'updater': {'last_run': 'today 05:00', 'mtime': now - 3 * 3600, 'applied': ['gluetun']},
        }
        with patch.object(incident_reports, '_gather', return_value=evidence):
            result = incident_reports.build(alert)
        text = debug_reports.describe_debug_report(result['report_id'])
        self.assertIn('Container gluetun: running, unhealthy', text)
        self.assertNotIn('not visible', text)
        self.assertTrue(any('applied new versions of gluetun' in c for c in result['suspected_causes']))

    def test_gather_survives_a_failing_source(self):
        with patch.object(incident_reports.nas_stack, 'poll_health', side_effect=RuntimeError('boom')), \
             patch.object(incident_reports.nas_stack, 'status_configured', return_value=False), \
             patch.object(incident_reports, '_media_sweep', return_value=['ok']), \
             patch.object(incident_reports, '_arr_logs', return_value=[]), \
             patch.object(incident_reports, '_prowlarr_logs', return_value=[]), \
             patch.object(incident_reports, '_qbit_logs', return_value=[]), \
             patch.object(incident_reports, '_ha_logs', return_value=[]):
            out = incident_reports._gather(time.monotonic() + 5)
        self.assertIsInstance(out['health'], RuntimeError)
        self.assertIsNone(out['status'])
        self.assertEqual(out['media'], ['ok'])

    def test_find_alert_rejects_an_id_from_before_a_restart(self):
        alert = event_bus.publish('arr_stack', 't', 'm', source='arr-stack')
        self.assertIs(incident_reports.find_alert(alert.id, alert.created_at), alert)
        self.assertIsNone(incident_reports.find_alert(alert.id, alert.created_at - 3600))
        self.assertIsNone(incident_reports.find_alert(alert.id + 1))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.report_patch = patch.object(debug_reports, '_REPORTS_FILE', Path(self.tempdir.name) / 'r.json')
        self.report_patch.start()
        self.token_patch = patch.dict(os.environ, {'JARVIS_API_TOKEN': 'test-token'})
        self.token_patch.start()
        event_bus.reset_for_tests()
        self.client = TestClient(server.app)
        self.headers = {'Authorization': 'Bearer test-token'}

    def tearDown(self):
        self.client.close()
        self.token_patch.stop()
        self.report_patch.stop()
        event_bus.reset_for_tests()
        self.tempdir.cleanup()

    def test_report_endpoint(self):
        alert = event_bus.publish('arr_stack', 'Jarvis — arr stack', 'Radarr down', source='arr-stack')
        self.assertEqual(self.client.post(f'/api/alerts/{alert.id}/report', json={}).status_code, 401)
        missing = self.client.post('/api/alerts/999/report', json={}, headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        fake = {'report_id': 'abc', 'prompt': 'p', 'title': 't', 'suspected_causes': [], 'sections': []}
        with patch.object(incident_reports, 'build', return_value=fake) as build:
            response = self.client.post(f'/api/alerts/{alert.id}/report',
                                        json={'created_at': alert.created_at, 'session_id': 's1'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['report_id'], 'abc')
        build.assert_called_once_with(alert, session_id='s1')
        caps = self.client.get('/api/capabilities', headers=self.headers).json()
        self.assertTrue(caps['capabilities']['chat']['incident_reports'])


if __name__ == '__main__':
    unittest.main()
