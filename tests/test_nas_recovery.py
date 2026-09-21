"""Execute the shell recovery against fake commands; never contact Docker/NAS."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'ops/nas/recover_stack.sh'

@unittest.skipUnless(sys.platform == 'linux' and shutil.which('flock') and shutil.which('jq'), 'Linux flock/procfs and jq required')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.trace = self.root / 'trace'
        (self.root / 'qbit_creds.env').write_text('QBIT_USER=test\nQBIT_PASS=test\n')
        self.script = self.root / 'recover.sh'
        source = SOURCE.read_text().replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin', f'PATH={self.bin}:/usr/bin:/bin')
        source = source.replace('[[ $EUID -ne 0 ]]', '[[ 0 -ne 0 ]]')
        source = source.replace('/volume1/docker/watchdog', str(self.root)).replace('[[ $EUID -eq 0 ]]', '[[ 0 -eq 0 ]]')
        self.script.write_text(source)
        self.script.chmod(0o755)
        self.env = {**os.environ, 'TRACE': str(self.trace)}
        self.command('docker', '''echo "docker $*" >> "$TRACE"
case "$*" in
  *"$FAIL_STEP"*) [ -z "${FAIL_STEP:-}" ] || exit 42 ;;
esac
case "$1" in inspect) echo healthy ;; esac
''')
        self.command('sleep', 'echo "sleep $*" >> "$TRACE"\n[ "${FAIL_STEP:-}" != "sleep $1" ] || exit 42\n')
        self.command('curl', '''case "$*" in
*transfer/info*) echo '{"dht_nodes":21}' ;;
*sync/maindata*) echo '{"server_state":{"dht_nodes":21}}' ;;
esac
''')

    def command(self, name, body):
        p = self.bin / name
        p.write_text('#!/bin/bash\n' + body)
        p.chmod(0o755)

    def run_recovery(self, **env):
        return subprocess.run([str(self.script), 'manual'], env={**self.env, **env}, capture_output=True, timeout=10)

    def test_order_and_success(self):
        self.assertEqual(self.run_recovery().returncode, 0)
        lines = self.trace.read_text().splitlines()
        self.assertEqual(lines[0], 'docker rm -f gluetun')
        self.assertTrue(lines[1].endswith('--force-recreate gluetun'))
        self.assertEqual(lines[2], 'sleep 20')
        self.assertEqual(lines[3], 'docker rm -f qbittorrent radarr sonarr prowlarr flaresolverr')
        self.assertTrue(lines[4].endswith('--force-recreate qbittorrent radarr sonarr prowlarr flaresolverr'))
        self.assertEqual(lines[5], 'sleep 130')
        self.assertTrue(all('--no-deps' in line for line in lines if 'compose' in line))
        status = json.loads((self.root / 'recovery-status.json').read_text())
        self.assertEqual((status['state'], status['dht_nodes']), ('success', 21))
        self.assertNotIn('mooney', self.trace.read_text())

    def test_each_chain_failure_aborts(self):
        cases = [
            ('rm -f gluetun', 1),
            ('--force-recreate gluetun', 2),
            ('sleep 20', 3),
            ('rm -f qbittorrent', 4),
            ('--force-recreate qbittorrent', 5),
            ('sleep 130', 6),
        ]
        for step, count in cases:
            with self.subTest(step=step):
                self.trace.write_text('')
                self.assertEqual(self.run_recovery(FAIL_STEP=step).returncode, 42)
                self.assertEqual(len(self.trace.read_text().splitlines()), count)
                self.assertEqual(json.loads((self.root / 'recovery-status.json').read_text())['state'], 'failed')

    def test_stale_dependent_conflict_is_cleared_before_recreate(self):
        # Reproduces the 2026-09-20 manual recovery: dead containers left
        # behind by a prior aborted run keep their names, and Compose refuses
        # to recreate them ("Conflict. The container name ... is already in
        # use") until they're explicitly removed first.
        marker = self.root / '.dependents_removed'
        self.command('docker', f'''echo "docker $*" >> "$TRACE"
case "$*" in
  *"--force-recreate qbittorrent"*)
    if [ ! -e {marker} ]; then
        echo "Error response from daemon: Conflict. The container name /qbittorrent is already in use" >&2
        exit 1
    fi
    ;;
  "rm -f qbittorrent radarr sonarr prowlarr flaresolverr") touch {marker} ;;
esac
case "$1" in inspect) echo healthy ;; esac
''')
        self.assertEqual(self.run_recovery().returncode, 0)
        lines = self.trace.read_text().splitlines()
        self.assertEqual(lines[3], 'docker rm -f qbittorrent radarr sonarr prowlarr flaresolverr')
        self.assertTrue(lines[4].endswith('--force-recreate qbittorrent radarr sonarr prowlarr flaresolverr'))
        self.assertEqual(json.loads((self.root / 'recovery-status.json').read_text())['state'], 'success')

    def test_verification_timeout_fails(self):
        self.script.write_text(self.script.read_text().replace('+ 300', '+ 0'))
        self.assertEqual(self.run_recovery().returncode, 1)
        self.assertEqual(json.loads((self.root / 'recovery-status.json').read_text())['phase'], 'verify')

    def test_inherited_lock_does_not_deadlock(self):
        result = subprocess.run(['bash', '-c', 'exec 9>"$1/stack.lock"; flock -n 9; "$1/recover.sh" indexer', '_', str(self.root)], env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0)

    def test_concurrent_lock_prevents_all_docker_calls(self):
        import fcntl
        with (self.root / 'stack.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_recovery().returncode, 75)
        self.assertFalse(self.trace.exists())

if __name__ == '__main__':
    unittest.main()
