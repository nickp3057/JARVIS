import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from brain import arr_stack
from integrations import nas_stack


class ArrMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'alerts.json'
        self.tracker = arr_stack.AlertTracker(self.path)

    def sample(self, nodes=0, issues=None):
        return {'dht_nodes': nodes, 'connection_status': 'connected', 'issues': issues or {}}

    def test_three_zero_checks_then_rate_limit_and_persist(self):
        self.assertEqual(self.tracker.notices(self.sample(), 100), [])
        self.assertEqual(self.tracker.notices(self.sample(), 160), [])
        self.assertIn('3 consecutive', self.tracker.notices(self.sample(), 220)[0])
        self.assertEqual(self.tracker.notices(self.sample(), 280), [])
        other = arr_stack.AlertTracker(self.path)
        other.notices(self.sample(), 300)
        other.notices(self.sample(), 360)
        self.assertEqual(other.notices(self.sample(), 420), [])
        self.assertTrue(other.notices(self.sample(), 2200))

    def test_unknown_or_positive_count_resets_consecutive_checks(self):
        for interruption in (None, 12):
            self.tracker.zeros = 0
            for nodes in (0, 0, interruption, 0, 0):
                self.assertEqual(self.tracker.notices(self.sample(nodes), 100), [])
            self.assertEqual(self.tracker.zeros, 2)

    def test_unreachable_alert_is_immediate_and_per_service(self):
        self.assertEqual(self.tracker.notices(self.sample(None, {'radarr-api': 'Radarr down'}), 100), ['Radarr down'])
        self.assertEqual(self.tracker.notices(self.sample(None, {'radarr-api': 'Radarr down', 'sonarr-api': 'Sonarr down'}), 160), ['Sonarr down'])

    def test_missing_dht_uses_sync_without_masking_connection(self):
        with patch.object(nas_stack.qbittorrent, 'transfer_info', return_value=(True, {'connection_status': 'connected'})), patch.object(nas_stack.qbittorrent, '_request', return_value=(True, {'server_state': {'dht_nodes': 14}})):
            ok, result = nas_stack.network_status()
        self.assertTrue(ok)
        self.assertEqual((result['dht_nodes'], result['connection_status']), (14, 'connected'))

    def test_invalid_dht_is_unknown_not_zero(self):
        with patch.object(nas_stack.qbittorrent, 'transfer_info', return_value=(True, {'dht_nodes': False})), patch.object(nas_stack.qbittorrent, '_request', return_value=(True, {'server_state': {'dht_nodes': '0'}})):
            self.assertIsNone(nas_stack.network_status()[1]['dht_nodes'])

    def test_api_health_and_readiness_failures_are_reported(self):
        response = Mock(status_code=200)
        response.json.return_value = {'status': 'ok', 'msg': 'ready'}
        with patch.object(nas_stack, 'network_status', return_value=(False, {'error': 'qBit down'})), patch.object(nas_stack.servarr_common, 'get_health', side_effect=[(True, [{'type': 'error', 'message': 'disk full'}]), (True, '<html>login</html>')]), patch.object(nas_stack.requests, 'get', return_value=response), patch.dict(os.environ, {'PROWLARR_URL': 'http://nas:9696', 'BYPARR_URL': 'http://nas:8191', 'PROWLARR_API_KEY': ''}):
            result = nas_stack.poll_health()
        self.assertIn('qbittorrent-api', result['issues'])
        self.assertIn('disk full', result['issues']['radarr-health'])
        self.assertIn('sonarr-api', result['issues'])
        self.assertNotIn('prowlarr-api', result['issues'])
        self.assertNotIn('byparr-api', result['issues'])

    def test_watchdog_note_recent_and_unknown(self):
        note = nas_stack.watchdog_note((True, {'observed_at': 5000, 'watchdog_actions': {'.last_action': 4800, '.rotate_cooldown': 0}, 'recovery': {'source': 'dht', 'state': 'success', 'updated_at': 4900}}))
        self.assertIn('DHT', note)
        self.assertIn('success', note)
        self.assertIn('unknown', nas_stack.watchdog_note((False, 'offline')))

    def test_phone_only_uses_configured_mobile_notify(self):
        with patch.object(nas_stack.home_assistant, 'call_service', return_value=(True, 'sent')) as call, patch.dict(os.environ, {'ARR_STACK_NOTIFY_SERVICE': 'notify.mobile_app_nicks_iphone_17_pro'}):
            self.assertTrue(nas_stack.notify_phone('DHT zero'))
        self.assertEqual(call.call_args.args, ('notify', 'mobile_app_nicks_iphone_17_pro'))


class RestartApprovalTests(unittest.TestCase):
    def setUp(self):
        arr_stack._pending.clear()
        self.env = patch.dict(os.environ, {'ARR_STACK_RESTART_ENABLED': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_prepare_cannot_start_and_other_session_cannot_confirm(self):
        with patch.object(nas_stack, 'start_recovery') as start:
            prompt = arr_stack.conversation_action('restart arr stack', 'a')
            phrase = prompt.split('exactly: ')[1]
            self.assertIn('kills active downloads', prompt)
            self.assertIn('invalid', arr_stack.conversation_action(phrase, 'b'))
            self.assertIsNone(arr_stack.conversation_action('yes', 'a'))
            start.assert_not_called()

    def test_confirmation_is_consumed_once(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        with patch.object(arr_stack, '_run_recovery', return_value='done') as run:
            self.assertEqual(arr_stack.conversation_action(phrase, 'a'), 'done')
            self.assertIn('invalid', arr_stack.conversation_action(phrase, 'a'))
            run.assert_called_once()

    def test_expired_and_cancelled_approvals_cannot_run(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        stamp, token, request = arr_stack._pending['a']
        arr_stack._pending['a'] = (stamp - 601, token, request)
        with patch.object(arr_stack, '_run_recovery') as run:
            self.assertIn('expired', arr_stack.conversation_action(phrase, 'a'))
            phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
            arr_stack.conversation_action('cancel arr restart', 'a')
            self.assertIn('invalid', arr_stack.conversation_action(phrase, 'a'))
            run.assert_not_called()

    def test_disabled_after_prepare_does_not_launch(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        with patch.dict(os.environ, {'ARR_STACK_RESTART_ENABLED': 'false'}), patch.object(arr_stack, '_run_recovery') as run:
            self.assertIn('disabled', arr_stack.conversation_action(phrase, 'a'))
            run.assert_not_called()

    def test_success_correlates_request_and_rechecks_live_dht(self):
        request = '11111111-1111-1111-1111-111111111111'
        with patch.object(nas_stack, 'start_recovery', return_value=(True, {'state': 'accepted', 'request_id': request})), patch.object(nas_stack, 'watchdog_status', side_effect=[(True, {'recovery': {'state': 'success', 'request_id': 'old'}}), (True, {'recovery': {'state': 'success', 'request_id': request, 'dht_nodes': 10, 'gluetun_health': 'healthy'}})]), patch.object(nas_stack, 'network_status', return_value=(True, {'dht_nodes': 19})), patch.object(arr_stack.time, 'sleep'):
            reply = arr_stack._run_recovery(request)
        self.assertIn('current DHT from the Optiplex: 19', reply)

    def test_launch_timeout_never_retries_or_claims_success(self):
        with patch.object(nas_stack, 'start_recovery', return_value=(False, 'timeout')) as start, patch.object(nas_stack, 'watchdog_status', return_value=(False, 'timeout')), patch.object(arr_stack.time, 'monotonic', side_effect=[0, 0, 45]), patch.object(arr_stack.time, 'sleep'):
            reply = arr_stack._run_recovery('11111111-1111-1111-1111-111111111111')
        self.assertIn('unknown', reply)
        start.assert_called_once()

    def test_failed_recovery_reports_phase_and_dht(self):
        request = '11111111-1111-1111-1111-111111111111'
        with patch.object(nas_stack, 'start_recovery', return_value=(True, {'state': 'accepted', 'request_id': request})), patch.object(nas_stack, 'watchdog_status', return_value=(True, {'recovery': {'state': 'failed', 'request_id': request, 'phase': 'verify', 'exit_code': 1, 'dht_nodes': 0}})):
            reply = arr_stack._run_recovery(request)
        self.assertIn('failed at verify', reply)
        self.assertIn('DHT: 0', reply)


class TransportTests(unittest.TestCase):
    def test_ssh_uses_fixed_args_and_pinned_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / 'key'; key.touch()
            hosts = Path(tmp) / 'hosts'; hosts.touch()
            with patch.dict(os.environ, {'NAS_SSH_HOST': '100.97.179.53', 'NAS_SSH_USER': 'Nick', 'NAS_STATUS_SSH_KEY': str(key), 'NAS_SSH_KNOWN_HOSTS': str(hosts)}), patch.object(subprocess, 'run', return_value=Mock(returncode=0, stdout='{}')) as run:
                self.assertTrue(nas_stack.watchdog_status()[0])
            args = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('IdentityAgent=none', args)
            self.assertEqual(args[-2:], ['Nick@100.97.179.53', 'status'])
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_bad_request_never_invokes_ssh(self):
        with patch.object(nas_stack, '_ssh') as ssh:
            self.assertFalse(nas_stack.start_recovery('id; reboot')[0])
            ssh.assert_not_called()

    def test_deadman_is_bounded_and_does_not_follow_redirect(self):
        with patch.dict(os.environ, {'JARVIS_HEALTHCHECKS_URL': 'https://hc-ping.com/example'}), patch.object(nas_stack.requests, 'get', return_value=Mock(status_code=200, text='OK')) as get:
            self.assertTrue(nas_stack.ping_deadman())
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with patch.dict(os.environ, {'JARVIS_HEALTHCHECKS_URL': 'http://evil.test/private'}), patch.object(nas_stack.requests, 'get') as get:
            self.assertFalse(nas_stack.ping_deadman())
            get.assert_not_called()

    def test_deadman_withholds_ping_for_dead_monitor(self):
        from brain.runtime import JarvisRuntime
        runtime = object.__new__(JarvisRuntime)
        runtime._arr_monitor_thread = Mock()
        runtime._arr_monitor_thread.is_alive.return_value = False
        runtime._arr_last_cycle = 0
        runtime._stop_monitors = Mock()
        runtime._stop_monitors.is_set.return_value = False
        runtime._stop_monitors.wait.return_value = True
        runtime.record_event = Mock()
        with patch.dict(os.environ, {'ARR_STACK_MONITOR_ENABLED': 'true'}), patch.object(nas_stack, 'ping_deadman') as ping:
            runtime._deadman_loop()
            ping.assert_not_called()
        runtime.record_event.assert_called_once()

    def test_long_turn_does_not_block_monitor_events(self):
        from collections import deque
        from brain.runtime import JarvisRuntime
        for streaming in (False, True):
            runtime = object.__new__(JarvisRuntime)
            runtime._lock = threading.RLock()
            runtime._turn_lock = threading.RLock()
            runtime._events = deque(maxlen=100)
            runtime._next_event_id = 1
            runtime.start_task = Mock(return_value=1)
            runtime.finish_task = Mock()
            entered, release, recorded = threading.Event(), threading.Event(), threading.Event()
            def long_turn(_):
                entered.set()
                release.wait(3)
                return 'recovery completed'
            def long_stream(text):
                yield long_turn(text)
            runtime.agent = Mock(last_used_tools=False)
            runtime.agent.respond.side_effect = long_turn
            runtime.agent.respond_streaming.side_effect = long_stream
            turn = threading.Thread(target=lambda: list(runtime.respond_streaming('confirm')) if streaming else runtime.respond('confirm'))
            turn.start()
            self.assertTrue(entered.wait(1))
            def event():
                runtime.record_event(kind='alert', source='arr-stack', title='service down')
                recorded.set()
            monitor = threading.Thread(target=event)
            monitor.start()
            try:
                self.assertTrue(recorded.wait(0.5), 'Monitor event blocked behind conversation lock')
            finally:
                release.set()
                turn.join(3)
                monitor.join(3)

    def test_agent_gate_precedes_model_for_both_modes(self):
        from brain.agent import JarvisAgent
        agent = object.__new__(JarvisAgent)
        agent._lock = threading.RLock()
        agent._client = Mock()
        with patch.object(agent, '_arr_stack_action', return_value='confirmation required'):
            self.assertEqual(agent.respond('restart arr stack'), 'confirmation required')
            self.assertEqual(list(agent.respond_streaming('restart arr stack')), ['confirmation required'])
        agent._client.messages.create.assert_not_called()
        agent._client.messages.stream.assert_not_called()

class ServerMonitorStartupTests(unittest.TestCase):
    def test_configured_monitor_starts_without_http_request(self):
        import asyncio
        import app.server as server
        for settings in ({'ARR_STACK_MONITOR_ENABLED': 'true', 'JARVIS_HEALTHCHECKS_URL': ''},
                         {'ARR_STACK_MONITOR_ENABLED': 'false', 'JARVIS_HEALTHCHECKS_URL': 'https://hc-ping.com/test'}):
            core = Mock()
            async def cycle():
                async with server.lifespan(server.app):
                    factory.assert_called_once()
            with patch.dict(os.environ, settings), patch.object(server, '_runtime', None), patch.object(server, 'JarvisRuntime', return_value=core) as factory:
                asyncio.run(cycle())
            core.stop_monitors.assert_called_once()

    def test_unconfigured_startup_remains_lazy(self):
        import asyncio
        import app.server as server
        async def cycle():
            async with server.lifespan(server.app):
                pass
        with patch.dict(os.environ, {'ARR_STACK_MONITOR_ENABLED': 'false', 'JARVIS_HEALTHCHECKS_URL': ''}), patch.object(server, '_runtime', None), patch.object(server, 'JarvisRuntime') as factory:
            asyncio.run(cycle())
            factory.assert_not_called()

    def test_parallel_requests_share_one_runtime(self):
        import app.server as server
        with patch.object(server, '_runtime', None), patch.object(server, 'JarvisRuntime', return_value=Mock()) as factory:
            threads = [threading.Thread(target=server.runtime) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
            factory.assert_called_once()

if __name__ == '__main__':
    unittest.main()
