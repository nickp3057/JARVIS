import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server
from brain import confirm_gate, self_repair, stack_fixes


class _InlineThread:
    """Runs the background stack fix synchronously so tests can assert on it."""

    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class StackFixApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [patch.object(self_repair, '_FILE', Path(self.tmp.name) / 'repairs.json'),
                   patch.object(self_repair, 'Thread', _InlineThread)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.notes = []
        self_repair.set_notifier(lambda title, detail, severity: self.notes.append((title, detail, severity)))
        self.addCleanup(self_repair.set_notifier, None)
        confirm_gate.clear_for_tests()
        self.addCleanup(confirm_gate.clear_for_tests)

    def _staged_id(self):
        return self_repair.public_proposals()[0]['id']

    def test_model_confirmation_alone_cannot_run_a_stack_fix(self):
        runner = MagicMock(return_value=(True, 'done'))
        with patch.dict(stack_fixes.FIXES, {'refresh_plex': stack_fixes.Fix('Rescan Plex', runner)}):
            self_repair.propose_stack_fix('refresh_plex', 'new files not showing')
            reply = self_repair.apply(self._staged_id(), confirmed=True)
        self.assertIn("needs Nick's own approval", reply)
        runner.assert_not_called()
        self.assertEqual(self_repair.public_proposals()[0]['status'], 'pending')

    def test_chat_yes_runs_the_fix_and_reports_the_verified_result(self):
        runner = MagicMock(return_value=(True, 'Every Prowlarr indexer passed its test.'))
        with patch.dict(stack_fixes.FIXES, {'retest_indexers': stack_fixes.Fix('Re-test indexers', runner)}):
            prompt = self_repair.propose_stack_fix('retest_indexers', 'two indexers backed off', 's1')
            self.assertIn('Reply "yes"', prompt)
            runner.assert_not_called()
            reply = confirm_gate.conversation_action('yes', 's1')
        runner.assert_called_once()
        self.assertIn('is running', reply)
        [proposal] = self_repair.public_proposals()
        self.assertEqual((proposal['status'], proposal['result']), ('applied', 'Every Prowlarr indexer passed its test.'))
        self.assertEqual(self.notes[0][2], 'info')
        # A second approval of the same id cannot run it twice.
        self.assertIn('already applied', self_repair.apply(proposal['id'], confirmed=True, approved_by='app'))

    def test_failed_verification_is_reported_as_failed(self):
        runner = MagicMock(return_value=(False, 'Home Assistant was restarted but is still not answering.'))
        with patch.dict(stack_fixes.FIXES, {'restart_home_assistant': stack_fixes.Fix('Restart HA', runner)}):
            self_repair.propose_stack_fix('restart_home_assistant', 'HA unreachable')
            self_repair.apply(self._staged_id(), confirmed=True, approved_by='app')
        self.assertEqual(self_repair.public_proposals()[0]['status'], 'failed')
        self.assertEqual(self.notes[0][2], 'warning')

    def test_unknown_fix_is_refused(self):
        self.assertIn('not an allowlisted', self_repair.propose_stack_fix('rm -rf', 'x'))
        self.assertEqual(self_repair.public_proposals(), [])


class FixVerificationTests(unittest.TestCase):
    def test_ha_restart_waits_until_it_answers(self):
        answers = iter([(False, 'down'), (False, 'down'), (True, 'ok')])
        with patch.object(stack_fixes.home_assistant, 'call_service', return_value=(True, '')), \
             patch.object(stack_fixes.home_assistant, 'test_connection', side_effect=lambda: next(answers)), \
             patch.object(stack_fixes, '_sleep'):
            ok, outcome = stack_fixes.run('restart_home_assistant')
        self.assertTrue(ok)
        self.assertIn('answering again', outcome)

    def test_indexer_retest_reports_what_is_still_down(self):
        response = MagicMock(status_code=400)
        with patch.dict(os.environ, {'PROWLARR_URL': 'http://p:9696', 'PROWLARR_API_KEY': 'k'}), \
             patch.object(stack_fixes.requests, 'post', return_value=response) as post, \
             patch.object(stack_fixes.nas_stack, 'indexer_outages', return_value={'indexer:2': 'Indexer Foo is down'}), \
             patch.object(stack_fixes, '_sleep'):
            ok, outcome = stack_fixes.run('retest_indexers')
        self.assertFalse(ok)
        self.assertIn('Indexer Foo is down', outcome)
        self.assertTrue(post.call_args.args[0].endswith('/api/v1/indexer/testall'))

    def test_arr_recovery_respects_the_restart_switch(self):
        with patch.dict(os.environ, {'ARR_STACK_RESTART_ENABLED': 'false'}):
            ok, outcome = stack_fixes.run('arr_recovery')
        self.assertFalse(ok)
        self.assertIn('disabled', outcome)


class RepairEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [patch.object(self_repair, '_FILE', Path(self.tmp.name) / 'repairs.json'),
                   patch.object(self_repair, 'Thread', _InlineThread),
                   patch.dict(os.environ, {'JARVIS_API_TOKEN': 'test-token'})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        server._runtime = MagicMock()
        self.addCleanup(setattr, server, '_runtime', None)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer test-token'}

    def test_list_approve_and_reject(self):
        runner = MagicMock(return_value=(True, 'Plex is rescanning 2 libraries, sir.'))
        with patch.dict(stack_fixes.FIXES, {'refresh_plex': stack_fixes.Fix('Rescan Plex', runner)}):
            self_repair.propose_stack_fix('refresh_plex', 'new episode missing')
            self.assertEqual(self.client.get('/api/repairs').status_code, 401)
            [item] = self.client.get('/api/repairs', headers=self.headers).json()['repairs']
            self.assertEqual((item['kind'], item['status']), ('stack_fix', 'pending'))
            body = self.client.post(f"/api/repairs/{item['id']}/apply", headers=self.headers).json()
        runner.assert_called_once()
        self.assertEqual(body['status'], 'applied')
        self_repair.propose_stack_fix('retest_indexers', 'x')
        pending = self.client.get('/api/repairs', headers=self.headers).json()['repairs'][0]
        self.client.post(f"/api/repairs/{pending['id']}/reject", headers=self.headers)
        statuses = {r['id']: r['status'] for r in self.client.get('/api/repairs', headers=self.headers).json()['repairs']}
        self.assertEqual(statuses[pending['id']], 'rejected')
        caps = self.client.get('/api/capabilities', headers=self.headers).json()['capabilities']
        self.assertTrue(caps['actions']['repairs'])


if __name__ == '__main__':
    unittest.main()
