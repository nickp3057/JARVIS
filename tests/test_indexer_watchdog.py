"""Exercise indexer recovery failures without Docker or the NAS."""
from pathlib import Path
import subprocess
import tempfile
import unittest

class IndexerWatchdogTests(unittest.TestCase):
    def test_failed_recovery_starts_cooldown(self):
        source = Path(__file__).resolve().parents[1] / "ops/nas/indexer_watchdog.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "qbit_creds.env").write_text("")
            (root / ".byparr_slow").write_text("1")
            recovery = root / "recover_stack.sh"
            recovery.write_text('#!/bin/bash\necho x >> "$(dirname "$0")/calls"\nexit 1\n')
            recovery.chmod(0o755)
            script = root / "watchdog.sh"
            text = source.read_text().replace("/volume1/docker/watchdog", str(root))
            stubs = "docker() { echo '{}'; }\nflock() { return 0; }\n"
            script.write_text(text.replace('exec 9>', stubs + 'exec 9>'))
            first = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=5)
            self.assertEqual(first.returncode, 1)
            self.assertTrue((root / ".rotate_cooldown").exists())
            second = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((root / "calls").read_text().splitlines(), ["x"])
