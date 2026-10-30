"""Run the real ops/nas/dht_watchdog.sh against stubbed curl/sudo/recovery."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'ops/nas/dht_watchdog.sh'

FAKE_CURL = r'''#!/bin/bash
jar=""; login=0; prev=""
for arg in "$@"; do
    [[ "$prev" == "-c" ]] && jar="$arg"
    [[ "$arg" == */auth/login ]] && login=1
    prev="$arg"
done
if [[ $login == 1 ]]; then
    [[ -n "$jar" ]] && echo "localhost FALSE / FALSE 0 QBT_SID abc" > "$jar"
    printf 200
else
    printf '{"server_state":{"dht_nodes":0}}'
fi
'''


class DhtWatchdogCooldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.wd, self.bin = root / 'wd', root / 'bin'
        self.wd.mkdir()
        self.bin.mkdir()
        (self.wd / 'qbit_creds.env').write_text('QBIT_USER=u\nQBIT_PASS=p\n')
        for name, body in {'curl': FAKE_CURL, 'flock': '#!/bin/bash\nexit 0\n', 'sudo': '#!/bin/bash\nexit 0\n'}.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        recover = self.wd / 'recover_stack.sh'
        recover.write_text('#!/bin/bash\necho x >> "$(dirname "$0")/calls"\nexit "$(cat "$(dirname "$0")/rc")"\n')
        recover.chmod(0o755)
        script = SOURCE.read_text().replace('WATCHDOG_DIR="/volume1/docker/watchdog"', f'WATCHDOG_DIR="{self.wd}"')
        self.script = root / 'dht_watchdog.sh'
        self.script.write_text(script)
        (self.wd / '.zero_count').write_text('2')

    def run_once(self, recovery_rc):
        (self.wd / 'rc').write_text(str(recovery_rc))
        env = {'PATH': f'{self.bin}:/usr/bin:/bin'}
        return subprocess.run(['bash', str(self.script)], env=env, capture_output=True, text=True, timeout=20)

    def calls(self):
        path = self.wd / 'calls'
        return len(path.read_text().splitlines()) if path.exists() else 0

    def test_failed_attempt_starts_cooldown_allowing_exactly_one_retry(self):
        self.assertEqual(self.run_once(1).returncode, 1)
        self.assertTrue((self.wd / '.last_action').exists(), 'cooldown must start on a failed attempt')
        self.assertEqual(self.calls(), 1)
        # Next check: one retry inside the window (this is what fixed 2026-09-22).
        self.run_once(1)
        self.assertEqual(self.calls(), 2)
        # Third check: retry used up, so no restart loop.
        result = self.run_once(1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), 2)
        self.assertIn('skipping remediation', result.stdout)

    def test_new_window_resets_retry_and_success_clears_state(self):
        self.run_once(1)
        self.run_once(1)
        (self.wd / '.last_action').write_text(str(int(time.time()) - 2000))
        self.assertEqual(self.run_once(0).returncode, 0)
        self.assertEqual(self.calls(), 3)
        for name in ('.zero_count', '.last_action_failed', '.retry_used'):
            self.assertFalse((self.wd / name).exists(), name)
        # Success keeps the cooldown so an immediate relapse waits.
        (self.wd / '.zero_count').write_text('2')
        self.run_once(0)
        self.assertEqual(self.calls(), 3)

    def test_http_calls_have_connect_and_total_timeouts(self):
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL.replace('jar=""; login=0; prev=""', r'''for flag in --connect-timeout --max-time; do
  case " $* " in *" $flag "*) ;; *) exit 90;; esac
done
jar=""; login=0; prev=""'''))
        result = self.run_once(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), 1)

    def test_success_first_time_has_no_retry_allowance(self):
        self.run_once(0)
        (self.wd / '.zero_count').write_text('2')
        self.run_once(0)
        self.assertEqual(self.calls(), 1)


if __name__ == '__main__':
    unittest.main()
