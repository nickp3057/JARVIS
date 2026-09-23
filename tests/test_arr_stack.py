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
        # Pre-hysteresis semantics (dedupe, escalation, grace) are exercised
        # with single-poll thresholds; DebounceTests covers the defaults.
        env = patch.dict(os.environ, {'ARR_STACK_ALERT_CONFIRM_CHECKS': '1', 'ARR_STACK_RECOVERY_CONFIRM_CHECKS': '1', 'ARR_STACK_DHT_ZERO_CHECKS': '3'})
        env.start()
        self.addCleanup(env.stop)
        self.tracker = arr_stack.AlertTracker(self.path)

    def sample(self, nodes=0, issues=None):
        return {'dht_nodes': nodes, 'connection_status': 'connected', 'issues': issues or {}}

    def texts(self, notices):
        return [n.text for n in notices]

    def test_three_zero_checks_then_persists_silently_until_recovered(self):
        self.assertEqual(self.tracker.notices(self.sample(), 100), [])
        self.assertEqual(self.tracker.notices(self.sample(), 160), [])
        third = self.tracker.notices(self.sample(), 220)
        self.assertEqual(len(third), 1)
        self.assertIn('3 consecutive', third[0].text)
        self.assertEqual((third[0].severity, third[0].kind), ('critical', 'new'))
        self.assertEqual(self.tracker.notices(self.sample(), 280), [])
        other = arr_stack.AlertTracker(self.path)
        self.assertEqual(other.notices(self.sample(), 300), [])
        # No cooldown re-fire: still active well within the escalation
        # window (default 6h) stays silent.
        self.assertEqual(other.notices(self.sample(), 10000), [])
        recovered = other.notices(self.sample(999), 10060)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].kind, 'recovered')
        self.assertIn('Recovered', recovered[0].text)
        self.assertEqual(other.notices(self.sample(999), 100120), [])

    def test_unknown_or_positive_count_resets_consecutive_checks(self):
        for interruption in (None, 12):
            self.tracker.zeros = 0
            for nodes in (0, 0, interruption, 0, 0):
                self.assertEqual(self.tracker.notices(self.sample(nodes), 100), [])
            self.assertEqual(self.tracker.zeros, 2)

    def test_unreachable_alert_is_immediate_and_per_service(self):
        first = self.tracker.notices(self.sample(None, {'radarr-api': 'Radarr down'}), 100)
        self.assertEqual(self.texts(first), ['Radarr down'])
        second = self.tracker.notices(self.sample(None, {'radarr-api': 'Radarr down', 'sonarr-api': 'Sonarr down'}), 160)
        self.assertEqual(self.texts(second), ['Sonarr down'])

    def test_new_condition_fires_once_then_silent_while_persisting(self):
        first = self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
        self.assertEqual(self.texts(first), ['Radarr down'])
        self.assertEqual(first[0].severity, 'critical')
        self.assertEqual(self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 160), [])
        self.assertEqual(self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 5000), [])

    def test_recovered_condition_fires_one_notice_then_silent(self):
        self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
        recovered = self.tracker.notices(self.sample(5, {}), 160)
        self.assertEqual(self.texts(recovered), ['Recovered: Radarr down'])
        self.assertEqual(recovered[0].kind, 'recovered')
        self.assertEqual(self.tracker.notices(self.sample(5, {}), 220), [])

    def test_condition_reappearing_after_recovery_is_new_again(self):
        # Post-recovery grace is a separate concern (see its own tests);
        # disable it here to isolate reappear-as-new dedup behavior.
        with patch.dict(os.environ, {'ARR_STACK_POST_RECOVERY_GRACE_S': '0'}):
            self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
            self.tracker.notices(self.sample(5, {}), 160)
            fresh = self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down again'}), 220)
        self.assertEqual(self.texts(fresh), ['Radarr down again'])
        self.assertEqual(fresh[0].kind, 'new')

    def test_restart_right_after_threshold_does_not_spuriously_recover(self):
        # A fresh AlertTracker (e.g. after a Jarvis restart) must not report
        # dht-zero as recovered just because its own in-memory streak
        # counter reset to 0, while DHT is in fact still zero.
        self.tracker.notices(self.sample(), 100)
        self.tracker.notices(self.sample(), 160)
        self.tracker.notices(self.sample(), 220)
        restarted = arr_stack.AlertTracker(self.path)
        self.assertEqual(restarted.notices(self.sample(), 280), [])

    def test_state_persists_and_survives_old_format(self):
        self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
        reloaded = arr_stack.AlertTracker(self.path)
        self.assertEqual(reloaded.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 160), [])
        self.path.write_text(json.dumps({'radarr-api': 100.0}))  # pre-dedup file shape
        migrated = arr_stack.AlertTracker(self.path)
        fresh = migrated.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 220)
        self.assertEqual(self.texts(fresh), ['Radarr down'])

    def test_radarr_sonarr_health_is_advisory_everything_else_critical(self):
        notices = self.tracker.notices(self.sample(5, {
            'radarr-health': 'Radarr health: indexer warning',
            'sonarr-health': 'Sonarr health: indexer warning',
            'radarr-api': 'Radarr down',
            'prowlarr-health': 'Prowlarr health: proxy failure',
        }), 100)
        severities = {n.key: n.severity for n in notices}
        self.assertEqual(severities['radarr-health'], 'advisory')
        self.assertEqual(severities['sonarr-health'], 'advisory')
        self.assertEqual(severities['radarr-api'], 'critical')
        self.assertEqual(severities['prowlarr-health'], 'critical')

    def test_escalation_fires_once_per_window_while_unresolved(self):
        with patch.dict(os.environ, {'ARR_STACK_ESCALATION_INTERVAL_S': '300'}):
            first = self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
            self.assertEqual(first[0].kind, 'new')
            # Still well within the window: silent.
            self.assertEqual(self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 200), [])
            escalated = self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 500)
            self.assertEqual(len(escalated), 1)
            self.assertEqual(escalated[0].kind, 'escalation')
            self.assertIn('unresolved', escalated[0].text)
            self.assertEqual(escalated[0].severity, 'critical')
            # Just escalated: silent again until the next window.
            self.assertEqual(self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 600), [])
            second = self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 900)
            self.assertEqual(len(second), 1)
            self.assertEqual(second[0].kind, 'escalation')

    def test_escalation_state_persists_across_restart(self):
        with patch.dict(os.environ, {'ARR_STACK_ESCALATION_INTERVAL_S': '300'}):
            self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
            restarted = arr_stack.AlertTracker(self.path)
            self.assertEqual(restarted.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 200), [])
            escalated = restarted.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 500)
            self.assertEqual([n.kind for n in escalated], ['escalation'])

    def test_post_recovery_grace_suppresses_new_condition_until_window_ends(self):
        with patch.dict(os.environ, {'ARR_STACK_POST_RECOVERY_GRACE_S': '300'}):
            self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
            recovered = self.tracker.notices(self.sample(5, {}), 160)  # critical recovery -> opens grace
            self.assertEqual([n.kind for n in recovered], ['recovered'])
            # A brand-new condition appearing inside the grace window is
            # tracked but not announced.
            self.assertEqual(self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 200), [])
            self.assertEqual(self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 400), [])
            # Grace (160 + 300 = 460) has now passed and it's still present.
            surfaced = self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 470)
            self.assertEqual([(n.text, n.kind) for n in surfaced], [('Sonarr down', 'new')])

    def test_condition_resolving_during_grace_before_announced_is_silent(self):
        with patch.dict(os.environ, {'ARR_STACK_POST_RECOVERY_GRACE_S': '300'}):
            self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down'}), 100)
            self.tracker.notices(self.sample(5, {}), 160)  # opens grace
            self.assertEqual(self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 200), [])
            # Resolves before it was ever announced: no notice either way.
            self.assertEqual(self.tracker.notices(self.sample(5, {}), 250), [])

    def test_grace_only_opens_on_critical_recovery_not_advisory(self):
        with patch.dict(os.environ, {'ARR_STACK_POST_RECOVERY_GRACE_S': '300'}):
            self.tracker.notices(self.sample(5, {'radarr-health': 'Radarr health: warning'}), 100)
            self.tracker.notices(self.sample(5, {}), 160)  # advisory recovery -> no grace
            fresh = self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 161)
            self.assertEqual([(n.text, n.kind) for n in fresh], [('Sonarr down', 'new')])

    def test_grace_does_not_suppress_escalation_of_already_active_conditions(self):
        with patch.dict(os.environ, {'ARR_STACK_POST_RECOVERY_GRACE_S': '300', 'ARR_STACK_ESCALATION_INTERVAL_S': '300'}):
            self.tracker.notices(self.sample(5, {'radarr-api': 'Radarr down', 'sonarr-api': 'Sonarr down'}), 100)
            self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 160)  # radarr-api recovers -> opens grace
            escalated = self.tracker.notices(self.sample(5, {'sonarr-api': 'Sonarr down'}), 500)
            self.assertEqual([(n.key, n.kind) for n in escalated], [('sonarr-api', 'escalation')])

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
        response.json.return_value = {'status': 'ok'}
        with patch.object(nas_stack, 'network_status', return_value=(False, {'error': 'qBit down'})), patch.object(nas_stack.servarr_common, 'get_health', side_effect=[(True, [{'type': 'error', 'message': 'disk full'}]), (True, '<html>login</html>')]), patch.object(nas_stack.requests, 'get', return_value=response), patch.dict(os.environ, {'PROWLARR_URL': 'http://nas:9696', 'PROWLARR_API_KEY': ''}):
            result = nas_stack.poll_health()
        self.assertIn('qbittorrent-api', result['issues'])
        self.assertIn('disk full', result['issues']['radarr-health'])
        self.assertIn('sonarr-api', result['issues'])
        self.assertNotIn('prowlarr-api', result['issues'])

    def test_byparr_is_not_checked_directly_prowlarr_health_reports_proxy_failure(self):
        # Byparr's :8191 is not published on the host by design (only
        # Prowlarr reaches it, over the internal Docker network), so a down
        # Byparr/indexer-proxy must surface through Prowlarr's own health
        # list instead of a direct check.
        response = Mock(status_code=200)
        response.json.return_value = [{'source': 'IndexerProxyCheck', 'type': 'error', 'message': 'Indexer proxy Byparr is unreachable'}]
        with patch.object(nas_stack, 'network_status', return_value=(True, {'dht_nodes': 5, 'connection_status': 'connected'})), \
             patch.object(nas_stack.servarr_common, 'get_health', return_value=(True, [])), \
             patch.object(nas_stack.requests, 'get', return_value=response), \
             patch.dict(os.environ, {'PROWLARR_URL': 'http://nas:9696', 'PROWLARR_API_KEY': 'key', 'BYPARR_URL': 'http://nas:8191'}):
            result = nas_stack.poll_health()
        self.assertNotIn('byparr-api', result['issues'])
        self.assertIn('Byparr', result['issues']['prowlarr-health'])

    def test_watchdog_note_recent_and_unknown(self):
        note = nas_stack.watchdog_note((True, {'observed_at': 5000, 'watchdog_actions': {'.last_action': 4800, '.rotate_cooldown': 0}, 'recovery': {'source': 'dht', 'state': 'success', 'updated_at': 4900}}))
        self.assertIn('DHT', note)
        self.assertIn('success', note)
        self.assertIn('unknown', nas_stack.watchdog_note((False, 'offline')))

    def test_phone_only_uses_configured_mobile_notify(self):
        with patch.object(nas_stack.home_assistant, 'call_service', return_value=(True, 'sent')) as call, patch.dict(os.environ, {'ARR_STACK_NOTIFY_SERVICE': 'notify.mobile_app_nicks_iphone_17_pro'}):
            self.assertTrue(nas_stack.notify_phone('DHT zero'))
        self.assertEqual(call.call_args.args, ('notify', 'mobile_app_nicks_iphone_17_pro'))


class DebounceTests(unittest.TestCase):
    """Default hysteresis: 3 polls to alert, 2 clean polls to recover."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'alerts.json'
        env = patch.dict(os.environ, {'ARR_STACK_POST_RECOVERY_GRACE_S': '0'})
        env.start()
        self.addCleanup(env.stop)
        for name in ('ARR_STACK_ALERT_CONFIRM_CHECKS', 'ARR_STACK_RECOVERY_CONFIRM_CHECKS', 'ARR_STACK_DHT_ZERO_CHECKS'):
            os.environ.pop(name, None)
        self.tracker = arr_stack.AlertTracker(self.path)

    def poll(self, t, issues=None, nodes=5, maintenance=False):
        return self.tracker.notices({'dht_nodes': nodes, 'connection_status': 'connected', 'issues': issues or {}, 'maintenance': maintenance}, t)

    def test_one_poll_blip_is_silent(self):
        down = {'radarr-api': 'Radarr down', 'sonarr-api': 'Sonarr down'}
        self.assertEqual(self.poll(0, down), [])
        self.assertEqual(self.poll(60), [])
        self.assertEqual(self.poll(120), [])
        self.assertEqual(self.tracker.pending, {})

    def test_alerts_on_third_consecutive_poll(self):
        down = {'radarr-api': 'Radarr down'}
        self.assertEqual(self.poll(0, down), [])
        self.assertEqual(self.poll(60, down), [])
        [notice] = self.poll(120, down)
        self.assertEqual((notice.key, notice.kind), ('radarr-api', 'new'))
        self.assertEqual(self.poll(180, down), [])

    def test_interrupted_streak_restarts_count(self):
        down = {'radarr-api': 'Radarr down'}
        self.poll(0, down)
        self.poll(60, down)
        self.poll(120)
        self.assertEqual(self.poll(180, down), [])
        self.assertEqual(self.poll(240, down), [])
        self.assertEqual(len(self.poll(300, down)), 1)

    def test_recovery_needs_two_clean_polls(self):
        down = {'radarr-api': 'Radarr down'}
        for t in (0, 60, 120):
            self.poll(t, down)
        self.assertEqual(self.poll(180), [])
        # Flapping back down during recovery confirmation keeps it active, silently.
        self.assertEqual(self.poll(240, down), [])
        self.assertEqual(self.poll(300), [])
        [notice] = self.poll(360)
        self.assertEqual(notice.kind, 'recovered')

    def test_maintenance_holds_alert_until_it_ends(self):
        down = {'radarr-api': 'Radarr down'}
        for t in (0, 60, 120, 180):
            self.assertEqual(self.poll(t, down, maintenance=True), [])
        # Recreated containers came back during maintenance: nothing to say.
        self.assertEqual(self.poll(240, maintenance=True), [])
        self.assertEqual(self.tracker.pending, {})

    def test_condition_outlasting_maintenance_alerts_immediately_after(self):
        down = {'radarr-api': 'Radarr down'}
        for t in (0, 60, 120, 180):
            self.poll(t, down, maintenance=True)
        self.assertEqual(len(self.poll(240, down)), 1)

    def test_dht_zero_default_threshold_outlasts_a_recovery(self):
        for t in range(0, 540, 60):
            self.assertEqual(self.poll(t, nodes=0), [])
        [notice] = self.poll(540, nodes=0)
        self.assertEqual(notice.key, 'dht-zero')

    def test_pending_survives_restart(self):
        down = {'radarr-api': 'Radarr down'}
        self.poll(0, down)
        self.poll(60, down)
        self.tracker = arr_stack.AlertTracker(self.path)
        self.assertEqual(len(self.poll(120, down)), 1)


class MaintenanceDetectionTests(unittest.TestCase):
    def test_running_recovery_is_maintenance_until_stale(self):
        status = (True, {'observed_at': 1000, 'recovery': {'state': 'running', 'updated_at': 990}})
        self.assertTrue(nas_stack.recovery_active(status))
        stale = (True, {'observed_at': 5000, 'recovery': {'state': 'running', 'updated_at': 990}})
        self.assertFalse(nas_stack.recovery_active(stale))

    def test_recently_finished_recovery_settles(self):
        status = (True, {'observed_at': 1200, 'recovery': {'state': 'success', 'updated_at': 1000}})
        self.assertTrue(nas_stack.recovery_active(status))
        self.assertFalse(nas_stack.recovery_active((True, {'observed_at': 2000, 'recovery': {'state': 'success', 'updated_at': 1000}})))

    def test_unknown_status_is_not_maintenance(self):
        self.assertFalse(nas_stack.recovery_active(None))
        self.assertFalse(nas_stack.recovery_active((False, 'ssh down')))
        self.assertFalse(nas_stack.recovery_active((True, {'recovery': None})))

    def test_status_not_configured_without_key(self):
        with patch.dict(os.environ, {'NAS_STATUS_SSH_KEY': '', 'NAS_SSH_HOST': 'nas'}):
            self.assertFalse(nas_stack.status_configured())


class IndexerOutageTests(unittest.TestCase):
    def setUp(self):
        nas_stack._indexer_names.update(at=0.0, base='', names={})

    def responses(self, mapping):
        def fake_get(url, **_):
            path = url.split('9696', 1)[1]
            response = Mock(status_code=200)
            response.raise_for_status = Mock()
            response.json.return_value = mapping[path]
            return response
        return fake_get

    def run_poll(self, mapping):
        with patch.object(nas_stack, 'network_status', return_value=(True, {'dht_nodes': 5, 'connection_status': 'connected'})), \
             patch.object(nas_stack.servarr_common, 'get_health', return_value=(True, [])), \
             patch.object(nas_stack.requests, 'get', side_effect=self.responses(mapping)), \
             patch.dict(os.environ, {'PROWLARR_URL': 'http://nas:9696', 'PROWLARR_API_KEY': 'key'}):
            return nas_stack.poll_health()

    def test_each_backed_off_indexer_is_its_own_condition(self):
        result = self.run_poll({
            '/api/v1/health': [{'source': 'IndexerStatusCheck', 'type': 'warning', 'message': 'Indexers unavailable due to failures: 1337x, EZTV'}],
            '/api/v1/indexerstatus': [
                {'indexerId': 1, 'disabledTill': '2999-01-01T00:00:00.1234567Z'},
                {'indexerId': 2, 'disabledTill': '2999-01-01T00:00:00Z'},
                {'indexerId': 3, 'disabledTill': '2000-01-01T00:00:00Z'},
            ],
            '/api/v1/indexer': [{'id': 1, 'name': '1337x', 'enable': True}, {'id': 2, 'name': 'EZTV', 'enable': True}, {'id': 3, 'name': 'YTS', 'enable': True}],
        })
        self.assertIn('1337x', result['issues']['indexer:1'])
        self.assertIn('EZTV', result['issues']['indexer:2'])
        self.assertNotIn('indexer:3', result['issues'])
        # The aggregate line is replaced, not duplicated.
        self.assertNotIn('prowlarr-health', result['issues'])

    def test_proxy_health_is_kept_alongside_indexers(self):
        result = self.run_poll({
            '/api/v1/health': [{'source': 'IndexerProxyStatusCheck', 'type': 'error', 'message': 'All indexer proxies are unavailable due to failures'}],
            '/api/v1/indexerstatus': [],
        })
        self.assertIn('proxies', result['issues']['prowlarr-health'])

    def test_user_disabled_indexer_is_ignored(self):
        result = self.run_poll({
            '/api/v1/health': [],
            '/api/v1/indexerstatus': [{'indexerId': 9, 'disabledTill': '2999-01-01T00:00:00Z'}],
            '/api/v1/indexer': [{'id': 9, 'name': 'Old', 'enable': False}],
        })
        self.assertEqual(result['issues'], {})

    def test_status_endpoint_failure_falls_back_to_aggregate(self):
        result = self.run_poll({
            '/api/v1/health': [{'source': 'IndexerStatusCheck', 'message': 'Indexers unavailable due to failures: 1337x'}],
            '/api/v1/indexerstatus': '<html>',
        })
        self.assertIn('1337x', result['issues']['prowlarr-health'])

    def test_indexer_names_are_cached_between_polls(self):
        mapping = {
            '/api/v1/health': [],
            '/api/v1/indexerstatus': [{'indexerId': 1, 'disabledTill': '2999-01-01T00:00:00Z'}],
            '/api/v1/indexer': [{'id': 1, 'name': '1337x'}],
        }
        fake = Mock(side_effect=self.responses(mapping))
        with patch.object(nas_stack, 'network_status', return_value=(True, {'dht_nodes': 5})), \
             patch.object(nas_stack.servarr_common, 'get_health', return_value=(True, [])), \
             patch.object(nas_stack.requests, 'get', fake), \
             patch.dict(os.environ, {'PROWLARR_URL': 'http://nas:9696', 'PROWLARR_API_KEY': 'key'}):
            nas_stack.poll_health()
            nas_stack.poll_health()
        paths = [c.args[0].split('9696')[1] for c in fake.call_args_list]
        self.assertEqual(paths.count('/api/v1/indexer'), 1)

    def test_indexer_notice_is_pushed_but_does_not_open_grace(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict(os.environ, {'ARR_STACK_ALERT_CONFIRM_CHECKS': '1', 'ARR_STACK_RECOVERY_CONFIRM_CHECKS': '1'}):
            tracker = arr_stack.AlertTracker(Path(tmp.name) / 'a.json')
            [down] = tracker.notices({'dht_nodes': 5, 'issues': {'indexer:1': 'Indexer 1337x is down'}}, 0)
            self.assertEqual(down.severity, 'warning')
            [up] = tracker.notices({'dht_nodes': 5, 'issues': {}}, 60)
            self.assertEqual(up.kind, 'recovered')
            self.assertEqual(tracker.grace_until, 0.0)


class RestartApprovalTests(unittest.TestCase):
    def setUp(self):
        arr_stack._pending.clear()
        # Patched _run_recovery never hands the lock to a follow-up thread.
        self.addCleanup(lambda: arr_stack._restart_lock.locked() and arr_stack._restart_lock.release())
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

    def test_reasking_reuses_open_token_instead_of_minting(self):
        first = arr_stack.prepare_restart('a').split('exactly: ')[1]
        again = arr_stack.conversation_action('restart the arr stack', 'a').split('exactly: ')[1]
        self.assertEqual(first, again)
        self.assertIn('already open', arr_stack.prepare_restart('a'))

    def test_bare_token_reprompts_with_same_token_and_does_not_run(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        token = phrase.split()[-1]
        with patch.object(arr_stack, '_run_recovery', return_value='done') as run:
            reply = arr_stack.conversation_action(token, 'a')
            self.assertIn('token without the phrase', reply)
            self.assertIn(phrase, reply)
            self.assertIn(f'You sent: "{token}"', reply)
            run.assert_not_called()
            # The original phrase is still valid afterwards.
            self.assertEqual(arr_stack.conversation_action(phrase, 'a'), 'done')

    def test_typo_in_phrase_echoes_input_and_keeps_request(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        token = phrase.split()[-1]
        typo = f'confirm restrat arr stack {token}'
        with patch.object(arr_stack, '_run_recovery') as run:
            reply = arr_stack.conversation_action(typo, 'a')
            self.assertIn(f'You sent: "{typo}"', reply)
            self.assertIn(phrase, reply)
            run.assert_not_called()
        self.assertEqual(arr_stack._pending['a'][1], token)

    def test_wrong_token_echoes_and_keeps_request(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        token = phrase.split()[-1]
        wrong = 'confirm restart arr stack ' + ('0' * 8 if token != '0' * 8 else '1' * 8)
        with patch.object(arr_stack, '_run_recovery') as run:
            reply = arr_stack.conversation_action(wrong, 'a')
            self.assertIn('does not match the open request', reply)
            self.assertIn(phrase, reply)
            run.assert_not_called()

    def test_unrelated_text_with_open_request_goes_to_model(self):
        arr_stack.prepare_restart('a')
        self.assertIsNone(arr_stack.conversation_action('what is the weather', 'a'))
        self.assertIsNone(arr_stack.conversation_action('yes', 'a'))

    def test_near_miss_without_open_request_goes_to_model(self):
        self.assertIsNone(arr_stack.conversation_action('confirm restrat arr stack deadbeef', 'a'))

    def test_disabled_after_prepare_does_not_launch(self):
        phrase = arr_stack.prepare_restart('a').split('exactly: ')[1]
        with patch.dict(os.environ, {'ARR_STACK_RESTART_ENABLED': 'false'}), patch.object(arr_stack, '_run_recovery') as run:
            self.assertIn('disabled', arr_stack.conversation_action(phrase, 'a'))
            run.assert_not_called()

    REQUEST = '11111111-1111-1111-1111-111111111111'
    ALL_UP = {'gluetun': (True, 'healthy'), 'qBittorrent': (True, 'DHT 19'), 'Radarr': (True, 'up'), 'Sonarr': (True, 'up'), 'Prowlarr': (True, 'up')}

    def clock(self, *values):
        return patch.object(arr_stack.time, 'monotonic', side_effect=list(values))

    def test_accepted_launch_replies_immediately_and_follows_up_in_background(self):
        arr_stack._restart_lock.acquire()
        reported = []
        arr_stack.set_recovery_notifier(lambda title, message: reported.append(message))
        self.addCleanup(arr_stack.set_recovery_notifier, None)
        with patch.object(nas_stack, 'start_recovery', return_value=(True, {'state': 'accepted', 'request_id': self.REQUEST})) as start, \
             patch.object(arr_stack, '_follow_recovery', return_value='Arr stack recovered, sir.') as follow:
            reply = arr_stack._run_recovery(self.REQUEST)
            for thread in threading.enumerate():
                if thread.name == 'arr-recovery-follow':
                    thread.join(2)
        self.assertIn('Restart accepted', reply)
        self.assertNotIn(self.REQUEST, reply)
        start.assert_called_once()
        follow.assert_called_once_with(self.REQUEST, True)
        self.assertEqual(reported, ['Arr stack recovered, sir.'])
        self.assertFalse(arr_stack._restart_lock.locked())

    def test_follow_up_crash_still_reports_and_releases_lock(self):
        arr_stack._restart_lock.acquire()
        reported = []
        arr_stack.set_recovery_notifier(lambda title, message: reported.append(message))
        self.addCleanup(arr_stack.set_recovery_notifier, None)
        with patch.object(arr_stack, '_follow_recovery', side_effect=RuntimeError('x')):
            arr_stack._follow_and_report(self.REQUEST, True)
        self.assertIn('lost track', reported[0])
        self.assertFalse(arr_stack._restart_lock.locked())

    def test_success_correlates_request_and_reports_every_service(self):
        with patch.object(nas_stack, 'watchdog_status', side_effect=[(True, {'recovery': {'state': 'success', 'request_id': 'old'}}), (True, {'recovery': {'state': 'success', 'request_id': self.REQUEST, 'dht_nodes': 10, 'gluetun_health': 'healthy'}})]), \
             patch.object(arr_stack, 'service_states', return_value=self.ALL_UP), patch.object(arr_stack.time, 'sleep'), self.clock(0, 0, 15):
            reply = arr_stack._follow_recovery(self.REQUEST, True)
        self.assertIn('recovered', reply)
        for name in ('gluetun', 'qBittorrent', 'Radarr', 'Sonarr', 'Prowlarr'):
            self.assertIn(name, reply)
        self.assertNotIn('unknown', reply)

    def test_without_status_key_live_probes_declare_success_after_chain_time(self):
        with patch.object(nas_stack, 'watchdog_status', return_value=(False, 'NAS restricted key or pinned known-hosts file is missing')), \
             patch.object(arr_stack, 'service_states', return_value=self.ALL_UP) as probe, patch.object(arr_stack.time, 'sleep'), \
             self.clock(0, 30, 90, 170):
            reply = arr_stack._follow_recovery(self.REQUEST, True)
        self.assertIn('back up', reply)
        # Never judged before the chain could have recreated everything.
        probe.assert_called_once()

    def test_deadline_reports_per_service_status_not_unknown(self):
        partial = dict(self.ALL_UP, Prowlarr=(False, 'unreachable'))
        with patch.object(nas_stack, 'watchdog_status', return_value=(False, 'missing')), \
             patch.object(arr_stack, 'service_states', return_value=partial), patch.object(arr_stack.time, 'sleep'), \
             self.clock(0, 200, 800):
            reply = arr_stack._follow_recovery(self.REQUEST, True)
        self.assertIn('Prowlarr failed (unreachable)', reply)
        self.assertIn('Radarr up', reply)
        self.assertNotIn('outcome is unknown', reply)

    def test_launch_timeout_never_retries_or_claims_success(self):
        with patch.object(nas_stack, 'start_recovery', return_value=(False, 'NAS SSH unavailable or timed out; outcome unknown')) as start, \
             patch.object(arr_stack, '_follow_recovery', return_value='x'):
            arr_stack._restart_lock.acquire()
            reply = arr_stack._run_recovery(self.REQUEST)
        self.assertIn("won't retry", reply)
        self.assertNotIn('accepted', reply)
        start.assert_called_once()

    def test_unconfirmed_launch_with_no_recovery_reports_nothing_started(self):
        with patch.object(nas_stack, 'watchdog_status', return_value=(False, 'missing')), \
             patch.object(arr_stack, 'service_states', return_value=self.ALL_UP), patch.object(arr_stack.time, 'sleep'), \
             self.clock(0, 0, 61):
            reply = arr_stack._follow_recovery(self.REQUEST, False)
        self.assertIn('never confirmed', reply)

    def test_failed_recovery_reports_phase_and_services(self):
        with patch.object(nas_stack, 'watchdog_status', return_value=(True, {'recovery': {'state': 'failed', 'request_id': self.REQUEST, 'phase': 'verify', 'exit_code': 1, 'dht_nodes': 0, 'gluetun_health': 'unhealthy'}})), \
             patch.object(nas_stack, 'network_status', return_value=(True, {'dht_nodes': 0})), \
             patch.object(arr_stack, '_probe_http', return_value=True), self.clock(0, 0):
            reply = arr_stack._follow_recovery(self.REQUEST, True)
        self.assertIn('failed at verify', reply)
        self.assertIn('gluetun failed (unhealthy)', reply)

    def test_service_states_infers_gluetun_from_qbit_dht(self):
        with patch.object(nas_stack, 'network_status', return_value=(True, {'dht_nodes': 42})), patch.object(arr_stack, '_probe_http', return_value=True):
            states = arr_stack.service_states()
        self.assertEqual(states['gluetun'], (True, 'tunnel passing traffic'))
        with patch.object(nas_stack, 'network_status', return_value=(False, {'error': 'x'})), patch.object(arr_stack, '_probe_http', return_value=False):
            states = arr_stack.service_states()
        self.assertFalse(states['gluetun'][0])
        self.assertFalse(states['Sonarr'][0])


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
