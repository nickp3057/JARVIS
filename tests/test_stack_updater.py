"""Exercise the real updater with all Docker/sleep/lock calls replaced locally."""
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "ops/nas/stack_updater.sh"
STUBS = r''' 
docker() {
  case "$*" in
    *"pull"*)
      echo "$*" >> "$WD/pulls.txt"
      [ "$FAIL" != pull ] || return 1
      case "$*" in *"pull $FAIL_PULL"*) [ -n "$FAIL_PULL" ] && return 1;; esac
      return 0;;
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
    def run_update(self, failure, fail_pull=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "watchdog").mkdir()
            (root / "plex").mkdir()
            source = SOURCE.read_text().replace("/volume1/docker", str(root))
            source = source.replace('exec 9>', 'FAIL=' + failure + '\nFAIL_PULL=' + fail_pull + '\n'
                                    + STUBS + '\nexec 9>', 1)
            script = root / "updater.sh"
            script.write_text(source)
            result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=5)
            self.pulls = (root / "watchdog/pulls.txt").read_text() if (root / "watchdog/pulls.txt").exists() else ""
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

    def test_one_image_that_wont_pull_is_retried_then_skipped(self):
        result, status = self.run_update("none", fail_pull="qbittorrent")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.pulls.count("pull qbittorrent"), 4)
        self.assertIn("PULL FAILED: qbittorrent", status)
        self.assertIn("APPLIED: gluetun", status)  # the rest still updated
        self.assertIn("PARTIAL:", status)
        self.assertNotIn("SUCCESS:", status)
