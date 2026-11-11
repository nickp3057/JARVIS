"""Settings › Laptop: the Core machine's battery from /sys/class/power_supply."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
from app import host_battery


def _supply(root: Path, name: str, **files: str) -> None:
    (root / name).mkdir()
    for key, value in files.items():
        (root / name / key).write_text(value + "\n")


class HostBatteryTests(unittest.TestCase):
    def test_held_at_charge_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "ADP1", type="Mains", online="1")
            _supply(root, "BAT1", type="Battery", capacity="49", status="Not charging")
            state = host_battery.status(root)
        self.assertEqual((state["present"], state["percent"], state["state"], state["plugged_in"]),
                         (True, 49, "not charging", True))

    def test_on_battery_and_no_battery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(host_battery.status(root)["present"])
            _supply(root, "ADP1", type="Mains", online="0")
            _supply(root, "BAT1", type="Battery", capacity="48", status="Discharging")
            self.assertEqual(host_battery.status(root)["plugged_in"], False)

    def test_endpoint_needs_the_token(self) -> None:
        with patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin"}):
            client = TestClient(server.app)
            self.addCleanup(client.close)
            self.assertEqual(client.get("/api/host/battery").status_code, 401)
            reply = client.get("/api/host/battery", headers={"Authorization": "Bearer admin"})
        self.assertEqual(reply.status_code, 200)
        self.assertIn("present", reply.json())
