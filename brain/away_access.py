"""Is the companion's away route up? (REMOTE_AND_MONEY_PLAN.md, Part 1, item 4)

Away from home the companion reaches Core through
``https://jarvis.nickolaspearson.com``: Lightsail's Caddy checks the device
certificate and proxies over the tailnet to Core. Core walks that whole loop
itself every ``AWAY_ACCESS_INTERVAL_S`` (600): out to Lightsail with its own
device certificate (the ``core`` cert from ``ops/remote/make-device-cert.sh``)
and back in to its own ``/health``. Two failures in a row raise one
"Away access is down" alert (Lightsail down, Caddy down, or Lightsail's
Tailscale dropped); the first success after that clears it.

Settings (.env): ``JARVIS_AWAY_URL`` (default the domain above),
``JARVIS_AWAY_CERT`` / ``JARVIS_AWAY_KEY`` (PEM paths). Without the cert the
monitor stays off: Caddy refuses anyone without one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from jarvis_logger import get_logger

log = get_logger(__name__)

DEFAULT_URL = "https://jarvis.nickolaspearson.com"
FAILURES_BEFORE_ALERT = 2


def url() -> str:
    return (os.getenv("JARVIS_AWAY_URL", "").strip() or DEFAULT_URL).rstrip("/")


def _cert() -> tuple[str, str] | None:
    cert = os.path.expanduser(os.getenv("JARVIS_AWAY_CERT", "").strip())
    key = os.path.expanduser(os.getenv("JARVIS_AWAY_KEY", "").strip())
    if cert and key and Path(cert).is_file() and Path(key).is_file():
        return cert, key
    return None


def configured() -> bool:
    return _cert() is not None


def check(timeout: float = 15.0) -> tuple[bool, str]:
    """One round trip through Lightsail to our own /health: (ok, detail)."""
    import requests
    cert = _cert()
    if cert is None:
        return False, "No device certificate for Core (JARVIS_AWAY_CERT/KEY)."
    token = os.getenv("JARVIS_API_TOKEN", "").strip()
    try:
        response = requests.get(f"{url()}/health", cert=cert, timeout=timeout,
                                headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as error:
        return False, f"{type(error).__name__}: {str(error)[:200]}"
    if response.status_code == 200:
        return True, "ok"
    return False, f"HTTP {response.status_code} from {url()}"


@dataclass
class AwayWatch:
    """Turns raw checks into at most one 'down' and one 'up' per outage."""
    failures: int = 0
    down: bool = False

    def step(self, ok: bool) -> str | None:
        if ok:
            self.failures = 0
            if self.down:
                self.down = False
                return "up"
            return None
        self.failures += 1
        if not self.down and self.failures >= FAILURES_BEFORE_ALERT:
            self.down = True
            return "down"
        return None
