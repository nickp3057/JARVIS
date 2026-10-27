import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server
import brain.debug_reports as debug_reports
from brain import confirm_gate, event_bus, incident_reports, self_repair, stack_fixes, tools
from brain.runtime import JarvisRuntime
from integrations import media_doctor

HASH = 'ABCDEF0123456789ABCDEF0123456789ABCDEF01'
RECORD = {'id': 77, 'movieId': 12, 'downloadId': HASH, 'title': 'The.Croods.2013.1080p.BluRay',
          'protocol': 'torrent', 'trackedDownloadState': 'downloading', 'trackedDownloadStatus': 'ok'}


def _queue(*records):
    return True, {'records': list(records)}


class _InlineThread:
    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class RetryReleaseTests(unittest.TestCase):
    def test_finds_the_queue_item_behind_a_torrent_hash(self):
        with patch.object(media_doctor.servarr, 'is_configured', return_value=True), \
             patch.object(media_doctor.servarr, 'get_queue', side_effect=[_queue(RECORD), _queue()]), \
             patch.object(media_doctor.servarr, '_queue_title', return_value='The Croods (2013)'):
            service, record, label = media_doctor.find_queue_record(HASH.lower())
        self.assertEqual((service, record['id'], label), ('radarr', 77, 'The Croods (2013)'))

    def test_retry_blocklists_and_confirms_a_new_grab(self):
        params = media_doctor.retry_params('radarr', RECORD, 'The Croods (2013)')
        replacement = dict(RECORD, id=78, title='The.Croods.2013.720p.WEB')
        with patch.object(media_doctor.servarr, 'request', return_value=(True, {})) as request, \
             patch.object(media_doctor.servarr, 'get_queue', return_value=_queue(replacement)):
            ok, outcome = media_doctor.retry_release(params, wait_s=0, sleep=lambda _: None)
        self.assertTrue(ok)
        self.assertIn('The.Croods.2013.720p.WEB', outcome)
        method, path = request.call_args.args[1:3]
        self.assertEqual((method, path), ('DELETE', '/api/v3/queue/77'))
        self.assertEqual(request.call_args.kwargs['params']['blocklist'], 'true')

    def test_retry_says_plainly_when_nothing_else_was_found(self):
        params = media_doctor.retry_params('radarr', RECORD, 'The Croods (2013)')
        with patch.object(media_doctor.servarr, 'request', return_value=(True, {})), \
             patch.object(media_doctor.servarr, 'get_queue', return_value=_queue()):
            ok, outcome = media_doctor.retry_release(params, wait_s=0, sleep=lambda _: None)
        self.assertFalse(ok)
        self.assertIn('no other acceptable release', outcome)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(self_repair, '_FILE', Path(self.tmp.name) / 'repairs.json'),
                  patch.object(self_repair, 'Thread', _InlineThread),
                  patch.object(debug_reports, '_REPORTS_FILE', Path(self.tmp.name) / 'reports.json')):
            p.start()
            self.addCleanup(p.stop)
        confirm_gate.clear_for_tests()
        event_bus.reset_for_tests()
        self.addCleanup(event_bus.reset_for_tests)

    def _found(self):
        return 'radarr', RECORD, 'The Croods (2013)'

    def test_stall_stages_one_retry_per_torrent_and_nothing_runs(self):
        runtime = JarvisRuntime.__new__(JarvisRuntime)
        runner = MagicMock(return_value=(True, 'grabbed'))
        with patch.object(media_doctor, 'find_queue_record', return_value=self._found()), \
             patch.dict(stack_fixes.FIXES, {'retry_download': stack_fixes.Fix('Retry', runner)}):
            self.assertTrue(runtime._stage_download_retry(HASH, 'metadata for 2 h'))
            self.assertTrue(runtime._stage_download_retry(HASH, 'metadata for 3 h'))
        [repair] = self_repair.public_proposals()
        self.assertEqual(repair['label'], 'Retry The Croods (2013): blocklist the stuck release and search for another')
        self.assertEqual(repair['status'], 'pending')
        runner.assert_not_called()

    def test_approving_the_staged_retry_runs_it_with_the_queue_params(self):
        runtime = JarvisRuntime.__new__(JarvisRuntime)
        with patch.object(media_doctor, 'find_queue_record', return_value=self._found()):
            runtime._stage_download_retry(HASH, 'stuck')
        repair_id = self_repair.public_proposals()[0]['id']
        with patch.object(media_doctor, 'retry_release', return_value=(True, 'Retried The Croods')) as retry, \
             patch.object(stack_fixes, '_sleep'):
            self_repair.apply(repair_id, confirmed=True, approved_by='app')
        self.assertEqual(retry.call_args.args[0]['queue_id'], 77)
        self.assertEqual(self_repair.public_proposals()[0]['status'], 'applied')

    def test_chat_retry_by_title_opens_the_confirm_gate(self):
        with patch.object(media_doctor, 'find_stalled_by_title', return_value=self._found()):
            reply = tools._tool_propose_stack_fix({'fix': 'retry_download', 'title': 'the croods',
                                                   'reason': 'no peers', '_jarvis_session_id': 's1'})
        self.assertIn('Retry The Croods', reply)
        self.assertIn('Reply "yes"', reply)

    def test_incident_report_for_a_stall_names_the_download_and_the_staged_retry(self):
        runtime = JarvisRuntime.__new__(JarvisRuntime)
        with patch.object(media_doctor, 'find_queue_record', return_value=self._found()):
            runtime._stage_download_retry(HASH, 'stuck')
        alert = event_bus.publish('download_stalled', 'Download stalled', 'The.Croods: metadata for 2 h',
                                  source='downloads', context={'torrent_hash': HASH})
        evidence = {'health': {'issues': {}, 'dht_nodes': 300, 'connection_status': 'connected'},
                    'status': None, 'media': [], 'radarr_log': [], 'sonarr_log': [], 'prowlarr_log': [],
                    'qbit_log': [], 'ha_log': [],
                    'download': ['The Croods (2013) in Radarr: stuck downloading: no peers']}
        with patch.object(incident_reports, '_gather', return_value=evidence):
            result = incident_reports.build(alert)
        text = debug_reports.describe_debug_report(result['report_id'])
        self.assertIn('This download:', text)
        self.assertIn('Retry staged as repair', text)
        self.assertIn('stuck in qBittorrent', result['suspected_causes'][0])


class LinkTests(unittest.TestCase):
    def test_links_carry_a_tailscale_url_when_mapped(self):
        env = {'JARVIS_API_TOKEN': 't', 'PLEX_URL': 'http://10.0.0.147:32400', 'RADARR_URL': 'http://other:7878',
               'RADARR_API_KEY': 'k', 'LINK_REMOTE_HOSTS': '10.0.0.147=100.97.179.53'}
        with patch.dict(os.environ, env):
            client = TestClient(server.app)
            body = client.get('/api/links', headers={'Authorization': 'Bearer t'}).json()
            client.close()
        by_name = {link['name']: link for link in body['links']}
        self.assertEqual(by_name['Plex']['remote_url'], 'http://100.97.179.53:32400')
        self.assertEqual(by_name['Radarr']['remote_url'], '')


if __name__ == '__main__':
    unittest.main()
