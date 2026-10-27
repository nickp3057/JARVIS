import hashlib
import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations import nas_host

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('host_health', ROOT / 'ops/nas/host_health.py')
host_health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host_health)

MDSTAT = """Personalities : [raid1]
md2 : active raid1 sda2[0]
      5844389888 blocks super 1.2 [1/1] [U]

md4 : active raid1 sdd2[1] sde2[0]
      1000 blocks super 1.2 [2/1] [U_]
      [=>...................]  recovery =  7.5% (75/1000) finish=10min

unused devices: <none>
"""


def _health(now, **extra):
    data = {'generated_at': now - 60, 'containers': [], 'raid': [], 'disks': [], 'volumes': [], 'errors': []}
    data.update(extra)
    return data


def _status(health, now):
    return True, {'observed_at': now, 'host_health': health}


class CollectorTests(unittest.TestCase):
    def test_mdstat_parsing_flags_degraded_and_sync(self):
        arrays = {a['name']: a for a in host_health.raid(MDSTAT)}
        self.assertFalse(arrays['md2']['degraded'])
        self.assertEqual(arrays['md2']['slots'], '1/1')
        self.assertTrue(arrays['md4']['degraded'])
        self.assertEqual(arrays['md4']['members'], ['sdd2', 'sde2'])
        self.assertEqual(arrays['md4']['sync'], 'recovery 7.5%')

    def test_containers_from_docker_inspect(self):
        inspect = [{'Name': '/plex', 'Config': {'Image': 'plexinc/pms-docker:latest'}, 'RestartCount': 2,
                    'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
                    'State': {'Status': 'running', 'Health': {'Status': 'healthy'},
                              'StartedAt': '2026-10-20T11:54:21.123456789Z', 'ExitCode': 0, 'OOMKilled': False}}]

        def runner(argv, timeout=30):
            return (0, 'abc\n') if argv[1] == 'ps' else (0, json.dumps(inspect))
        [plex] = host_health.containers(runner)
        self.assertEqual((plex['name'], plex['health'], plex['restart_count']), ('plex', 'healthy', 2))
        self.assertEqual(plex['restart_policy'], 'unless-stopped')
        self.assertIsInstance(plex['started_at'], int)

    def test_smart_tells_standby_from_open_failure(self):
        def reply(message):
            return lambda argv, timeout=30: (2, json.dumps({'smartctl': {'messages': [{'string': message}]}}))
        self.assertTrue(host_health.smart('sda', reply('Device is in STANDBY mode, exit(2)'))['standby'])
        failed = host_health.smart('sda', reply('Smartctl open device: /dev/sda failed: Permission denied'))
        self.assertFalse(failed['standby'])
        self.assertIn('Permission denied', failed['error'])

    def test_smart_attributes(self):
        payload = {'model_name': 'ST2000DM001', 'smart_status': {'passed': True}, 'temperature': {'current': 41},
                   'power_on_time': {'hours': 30000},
                   'ata_smart_attributes': {'table': [{'id': 5, 'raw': {'value': 8}}, {'id': 197, 'raw': {'value': 0}}]}}
        disk = host_health.smart('sdb', lambda argv, timeout=30: (0, json.dumps(payload)))
        self.assertEqual((disk['reallocated'], disk['pending'], disk['temp_c']), (8, 0, 41))

    def test_logs_fans_and_updater(self):
        lines = ['Oct 27 kernel: ata1.00: exception Emask 0x0', 'Oct 27 kernel: eth0 up',
                 'Oct 27 kernel: blk_update_request: I/O error, dev sdb']
        self.assertEqual(host_health.kernel_disk_errors(lines)['count'], 2)
        fan = host_health.fans(['\x1b[31m[10-27 10:04:26][hwmonitor.c checkFan()][WARN]\x1b[0m f1=660,f2=-1,t=740'])
        self.assertEqual((fan['f1_rpm'], fan['f2_rpm']), (660, -1))
        self.assertNotIn('\x1b', fan['line'])
        update = host_health.updater('Last run: 2026-10-25 05:00:36\nAPPLIED: gluetun tailscale\n', 1792926261)
        self.assertEqual(update['applied'], ['gluetun', 'tailscale'])


class HostWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in ('NAS_HOST_CONFIRM_CHECKS', 'NAS_HOST_IGNORE_CONTAINERS', 'NAS_VOLUME_ALERT_PCT'):
            os.environ.pop(key, None)

    def test_container_down_needs_two_polls_then_recovers_quietly(self):
        watch, now = nas_host.HostWatch(self.path), 10_000.0
        down = _health(now, containers=[{'name': 'pihole', 'state': 'exited', 'exit_code': 1,
                                          'restart_policy': 'unless-stopped', 'restart_count': 0}])
        self.assertEqual(watch.step(_status(down, now), now), [])
        [alert] = watch.step(_status(down, now + 300), now + 300)
        self.assertIn('pihole is exited', alert.title)
        self.assertTrue(alert.push)
        self.assertEqual(watch.step(_status(down, now + 600), now + 600), [])
        up = _health(now, containers=[{'name': 'pihole', 'state': 'running', 'restart_count': 0}])
        self.assertEqual(watch.step(_status(up, now + 900), now + 900), [])
        [recovered] = watch.step(_status(up, now + 1200), now + 1200)
        self.assertEqual((recovered.severity, recovered.push), ('info', False))
        # State persists across a jarvis-api restart.
        self.assertEqual(nas_host.HostWatch(self.path).state['alerted'], {})

    def test_degraded_raid_alerts_immediately_as_critical(self):
        now = 10_000.0
        health = _health(now, raid=host_health.raid(MDSTAT))
        [alert] = nas_host.HostWatch(self.path).step(_status(health, now), now)
        self.assertEqual(alert.severity, 'critical')
        self.assertIn('md4 is degraded', alert.message)

    def test_arr_containers_are_ignored_by_default(self):
        now = 10_000.0
        health = _health(now, containers=[{'name': 'radarr', 'state': 'exited', 'restart_policy': 'unless-stopped'}])
        self.assertEqual(nas_host.conditions(health, now), {})

    def test_restart_loop_detected_from_restart_counts(self):
        watch, now = nas_host.HostWatch(self.path), 10_000.0
        for i, count in enumerate((5, 6, 7)):
            health = _health(now, containers=[{'name': 'immich_server', 'state': 'running', 'restart_count': count}])
            alerts = watch.step(_status(health, now + i * 300), now + i * 300)
        self.assertEqual(alerts, [])
        health = _health(now, containers=[{'name': 'immich_server', 'state': 'running', 'restart_count': 8}])
        [alert] = watch.step(_status(health, now + 900), now + 900)
        self.assertIn('restarted 3 times', alert.message)

    def test_kernel_errors_baseline_then_new_lines(self):
        watch, now = nas_host.HostWatch(self.path), 10_000.0
        old = _health(now, kernel_disk_errors={'count': 1, 'recent': ['old I/O error']})
        self.assertEqual(watch.step(_status(old, now), now), [])
        new = _health(now, kernel_disk_errors={'count': 2, 'recent': ['old I/O error', 'new I/O error, dev sdb']})
        [alert] = watch.step(_status(new, now + 300), now + 300)
        self.assertIn('1 new', alert.message)
        self.assertEqual(watch.step(_status(new, now + 600), now + 600), [])

    def test_stale_snapshot_and_full_volume(self):
        now = 10_000.0
        health = _health(now, volumes=[{'path': '/volume2', 'used_pct': 98.0, 'free_bytes': 50e9}])
        health['generated_at'] = now - 3600
        found = nas_host.conditions(health, now)
        self.assertEqual(found['volume:/volume2'][0], 'critical')
        self.assertIn('not running', found['host-health-stale'][1])

    def test_no_status_or_no_snapshot_is_not_judged(self):
        watch = nas_host.HostWatch(self.path)
        self.assertEqual(watch.step(None), [])
        self.assertEqual(watch.step((False, 'timed out')), [])
        self.assertEqual(watch.step((True, {'observed_at': 1, 'host_health': None})), [])

    def test_describe_and_causes_name_a_fresh_image_update(self):
        now = 1_793_000_000.0
        health = _health(now, updater={'last_run': '2026-10-25 05:00:36', 'mtime': now - 13 * 3600,
                                       'applied': ['gluetun', 'tailscale']},
                         raid=[{'name': 'md2', 'level': 'raid1', 'slots': '1/1', 'pattern': 'U', 'degraded': False}],
                         disks=[{'device': 'sdb', 'model': 'ST2000DM001', 'smart_passed': True, 'reallocated': 8}])
        text = '\n'.join(nas_host.describe(health, now))
        self.assertIn('single disk, no redundancy', text)
        self.assertIn('8 reallocated', text)
        causes = nas_host.causes(health, now, now)
        self.assertTrue(any('gluetun, tailscale 13 h before' in c for c in causes))
        self.assertTrue(any('bad sectors' in c for c in causes))


class InstallerTests(unittest.TestCase):
    def test_installer_pins_the_current_stack_status_hash(self):
        installer = (ROOT / 'ops/nas/install-host-health.sh').read_text()
        current = hashlib.sha256((ROOT / 'ops/nas/stack_status.sh').read_bytes()).hexdigest()
        pinned = re.findall(r'\b[0-9a-f]{64}\b', installer)
        self.assertIn(current, pinned)
        # The 2026-09-20 version that is live on the NAS today.
        self.assertIn('8b5281742aef911ec8f1c495a0a90acd2906bea9177179c31e791240f388f583', pinned)


if __name__ == '__main__':
    unittest.main()
