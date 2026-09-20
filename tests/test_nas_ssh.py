"""Verify forced-command rejection without running sudo or recovery."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'ops/nas'

class RestrictedSSHTests(unittest.TestCase):
    def run_command(self, name, command, *args):
        source = (ROOT / name).read_text()
        # Exercise the unprivileged forced-command branch even under root CI.
        source = source.replace('[[ $EUID -ne 0 ]]', '[[ 1 -ne 0 ]]')
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'script'
            p.write_text(source)
            return subprocess.run(['bash', str(p), *args], env={**os.environ, 'SSH_ORIGINAL_COMMAND': command}, capture_output=True, text=True, timeout=5)

    def test_recovery_probe_is_harmless(self):
        result = self.run_command('recover_stack.sh', 'probe')
        self.assertEqual(result.returncode, 0)
        self.assertIn('no restart performed', result.stdout)

    def test_shell_scp_empty_and_injection_rejected(self):
        for command in ('', 'id', '/bin/bash', 'scp -t /tmp/file', 'recover x; id', 'recover ' + 'a' * 36, 'status'):
            with self.subTest(command=command):
                self.assertEqual(self.run_command('recover_stack.sh', command).returncode, 64)

    def test_recovery_script_rejects_arguments(self):
        self.assertEqual(self.run_command('recover_stack.sh', 'probe', 'manual').returncode, 64)

    def test_readonly_key_rejects_recovery_and_shell(self):
        for command in ('recover 11111111-1111-1111-1111-111111111111', 'id', 'status; id'):
            self.assertEqual(self.run_command('stack_status.sh', command).returncode, 64)

    def test_launcher_rejects_invalid_or_extra_arguments_before_io(self):
        for args in ([], ['bad'], ['11111111-1111-1111-1111-111111111111', 'id']):
            result = subprocess.run(['bash', str(ROOT / 'jarvis-nas-launch'), *args], capture_output=True)
            self.assertEqual(result.returncode, 64)

if __name__ == '__main__':
    unittest.main()
