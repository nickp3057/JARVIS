"""Exercise the real updater with all Docker/sleep/lock calls replaced locally."""
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "ops/nas/stack_updater.sh"
STUBS = r''' 
docker() {
  case "$*" in
    *"pull"*) [ "$FAIL" != pull ]; return $?;;
    *"up -d"*) [ "$FAIL" != recreate ]; return $?;;
    'inspect --format {{.Image}} gluetun') echo old;;
    'image inspect --format {{.Id}} gluetun:latest') echo new;;
    'inspect --format {{.Config.Image}} '*) echo "${@: -1}:latest";;
    'inspect --format {{.Image}} '*) echo same;;
    'image inspect --format {{.Id}} '*) echo same;;
    *) return 0;;
  esac
}
flock() { return 0; }
sleep() { return 0; }
'''

class StackUpdaterTests(unittest.TestCase):
    def run_update(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "watchdog").mkdir()
            (root / "plex").mkdir()
            source = SOURCE.read_text().replace("/volume1/docker", str(root))
            source = source.replace('exec 9>', 'FAIL=' + failure + '\n' + STUBS + '\nexec 9>')
            script = root / "updater.sh"
            script.write_text(source)
            result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=5)
            return result, (root / "watchdog/updater_status.txt").read_text()

    def test_failed_pull_or_recreate_never_reports_applied_or_success(self):
        for failure in ("pull", "recreate"):
            with self.subTest(failure=failure):
                result, status = self.run_update(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FAILED:", status)
                self.assertNotIn("APPLIED:", status)
                self.assertNotIn("SUCCESS:", status)

    def test_successful_update_records_applied(self):
        result, status = self.run_update("none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("APPLIED: gluetun", status)
        self.assertIn("SUCCESS:", status)
