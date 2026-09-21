"""Execute install-recovery.sh's hash-pin/apply logic against fake live files."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'ops/nas/install-recovery.sh'
REAL_DHT = (Path(__file__).resolve().parents[1] / 'ops/nas/dht_watchdog.sh').read_bytes()
REAL_INDEXER = (Path(__file__).resolve().parents[1] / 'ops/nas/indexer_watchdog.sh').read_bytes()
REAL_RECOVER = (Path(__file__).resolve().parents[1] / 'ops/nas/recover_stack.sh').read_bytes()


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('flock') and shutil.which('sha256sum'), 'Linux flock and sha256sum required')
class InstallRecoveryHashPinTests(unittest.TestCase):
    """The installer must accept either the original pre-refactor live
    scripts it was first reviewed against, or the already-refactored
    versions it wrote on a prior --apply run (so re-running it later, e.g.
    to pick up a recover_stack.sh-only change, isn't mistaken for
    unreviewed drift) — and must still fail closed on anything else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.here = root / 'here'
        self.wd = root / 'wd'
        self.bin = root / 'bin'
        self.here.mkdir()
        self.wd.mkdir()
        self.bin.mkdir()
        (self.here / 'recover_stack.sh').write_bytes(REAL_RECOVER)
        (self.here / 'dht_watchdog.sh').write_bytes(REAL_DHT)
        (self.here / 'indexer_watchdog.sh').write_bytes(REAL_INDEXER)

        # Stand-ins for content we can't reproduce (the real 2026-09-20
        # pre-refactor scripts were only ever hashed, never stored here).
        # Substituting their pinned hash for a hash of content we control
        # still exercises the real accept-either-hash / fail-closed logic.
        self.original_dht = b'#!/bin/bash\n# stand-in for the pre-refactor dht_watchdog.sh\n'
        self.original_indexer = b'#!/bin/bash\n# stand-in for the pre-refactor indexer_watchdog.sh\n'
        original_dht_sha = hashlib.sha256(self.original_dht).hexdigest()
        original_indexer_sha = hashlib.sha256(self.original_indexer).hexdigest()

        source = SOURCE.read_text()
        source = source.replace('WD=/volume1/docker/watchdog', f'WD={self.wd}')
        source = source.replace('[[ $EUID == 0 ]]', '[[ 0 == 0 ]]')
        source = source.replace(
            '832e2678f6cf1d4368507faa23691b18eae6ab649c47e49ef43f64302a72dda3',
            original_dht_sha,
        )
        source = source.replace(
            '30d7e41cbcbe14654a367d0a1d7deef783f6eae1ed02cb77464e8dc844a4b7a7',
            original_indexer_sha,
        )
        self.script = self.here / 'install-recovery.sh'
        self.script.write_text(source)
        self.script.chmod(0o755)
        # Not actually root in the test sandbox; chown would otherwise fail.
        (self.bin / 'chown').write_text('#!/bin/bash\nexit 0\n')
        (self.bin / 'chown').chmod(0o755)
        self.env = {'PATH': f'{self.bin}:/usr/bin:/bin'}

    def run_apply(self):
        return subprocess.run([str(self.script), '--apply'], env=self.env, capture_output=True, text=True, timeout=10)

    def test_accepts_original_pin(self):
        (self.wd / 'dht_watchdog.sh').write_bytes(self.original_dht)
        (self.wd / 'indexer_watchdog.sh').write_bytes(self.original_indexer)
        result = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.wd / 'dht_watchdog.sh').read_bytes(), REAL_DHT)
        self.assertEqual((self.wd / 'indexer_watchdog.sh').read_bytes(), REAL_INDEXER)

    def test_accepts_already_refactored(self):
        # Reproduces the 2026-09-20 re-run failure directly: the live files
        # already are this installer's own prior output.
        (self.wd / 'dht_watchdog.sh').write_bytes(REAL_DHT)
        (self.wd / 'indexer_watchdog.sh').write_bytes(REAL_INDEXER)
        result = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.wd / 'recover_stack.sh').read_bytes(), REAL_RECOVER)

    def test_rejects_unreviewed_drift(self):
        (self.wd / 'dht_watchdog.sh').write_bytes(b'#!/bin/bash\n# someone edited this by hand\n')
        (self.wd / 'indexer_watchdog.sh').write_bytes(self.original_indexer)
        result = self.run_apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unreviewed change', result.stderr)
        self.assertFalse((self.wd / 'recover_stack.sh').exists())

    def test_rejects_missing_live_file(self):
        (self.wd / 'indexer_watchdog.sh').write_bytes(self.original_indexer)
        result = self.run_apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing expected live file', result.stderr)


if __name__ == '__main__':
    unittest.main()
