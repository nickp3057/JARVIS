"""Outbound phone notifications: JARVIS companion push first, HA as fallback.

Every proactive alert that should reach Nick's phone goes through ``send()``.
Channels are tried in order and the first successful one wins:

1. ``app_push`` -- APNs push to the JarvisCompanion iOS app. **Stubbed.**
   The server side is ready (device tokens are registered through
   ``POST /api/push/register`` and stored in ``memory/push_devices.json``),
   but no APNs request is ever made yet: that needs an Apple Developer
   Program membership, an APNs auth key (.p8), the Push Notifications
   capability on the app, and an HTTP/2 client. ``app_push_status()`` says
   exactly which piece is missing. Until ``APNS_ENABLED=true`` *and* a real
   sender replaces ``_send_apns``, this channel reports not-ready and is
   skipped, so Home Assistant stays the working primary.
2. ``home_assistant`` -- HA ``notify.mobile_app_*`` (the existing, working
   path). Service name from ``JARVIS_NOTIFY_HA_SERVICE``, falling back to the
   older ``ARR_STACK_NOTIFY_SERVICE``.

Settings are read live on every call, like the rest of the project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
from typing import NamedTuple

from jarvis_logger import get_logger

log = get_logger(__name__)

_DEVICES_FILE = Path(__file__).resolve().parents[1] / "memory" / "push_devices.json"
_lock = threading.Lock()
_TOKEN_RE = re.compile(r"[0-9a-fA-F]{64,200}")


class Delivery(NamedTuple):
    delivered: bool
    channel: str | None
    detail: str


# --- Device registry ------------------------------------------------------

def _load_devices() -> dict[str, dict]:
    try:
        data = json.loads(_DEVICES_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_devices(devices: dict[str, dict]) -> None:
    _DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _DEVICES_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(devices, indent=2))
    temporary.replace(_DEVICES_FILE)


def register_device(token: str, environment: str = "production", name: str = "") -> tuple[bool, str]:
    """Store an APNs device token reported by the companion app."""
    token = (token or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        return False, "Device token must be the hex APNs token"
    environment = environment if environment in {"production", "sandbox"} else "production"
    with _lock:
        devices = _load_devices()
        devices[token.lower()] = {"environment": environment, "name": (name or "")[:80], "registered_at": time.time()}
        _save_devices(devices)
    return True, "registered"


def unregister_device(token: str) -> bool:
    with _lock:
        devices = _load_devices()
        removed = devices.pop((token or "").strip().lower(), None) is not None
        if removed:
            _save_devices(devices)
    return removed


def device_count() -> int:
    with _lock:
        return len(_load_devices())


# --- Channels -------------------------------------------------------------

def app_push_status() -> tuple[bool, str]:
    """Whether companion push can be used, and if not, the first missing piece."""
    if os.getenv("APNS_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "APNS_ENABLED is off (Apple Developer Program + APNs key not set up yet)"
    missing = [name for name in ("APNS_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID") if not os.getenv(name, "").strip()]
    if missing:
        return False, "Missing " + ", ".join(missing)
    if not Path(os.getenv("APNS_KEY_PATH", "").strip()).is_file():
        return False, "APNS_KEY_PATH does not point at a .p8 file"
    if device_count() == 0:
        return False, "No companion device has registered a push token"
    return False, "APNs sender not implemented yet (see brain/notifications.py)"


def _send_apns(title: str, message: str) -> tuple[bool, str]:
    # Stub. A real sender signs an ES256 JWT with the .p8 key (kid=APNS_KEY_ID,
    # iss=APNS_TEAM_ID), then POSTs {"aps": {"alert": {"title", "body"},
    # "sound": "default"}} over HTTP/2 to api.push.apple.com (or
    # api.sandbox.push.apple.com for sandbox tokens) at /3/device/<token> with
    # apns-topic=APNS_BUNDLE_ID and apns-push-type=alert, dropping tokens that
    # come back 410 Unregistered. Needs httpx[http2] + PyJWT[crypto].
    return False, "APNs sender not implemented"


def ha_service() -> str:
    service = (os.getenv("JARVIS_NOTIFY_HA_SERVICE", "").strip() or os.getenv("ARR_STACK_NOTIFY_SERVICE", "").strip())
    service = service.removeprefix("notify.")
    return service if re.fullmatch(r"mobile_app_[a-z0-9_]+", service) else ""


def _send_home_assistant(title: str, message: str) -> tuple[bool, str]:
    service = ha_service()
    if not service:
        return False, "No HA mobile_app notify service configured"
    from integrations import home_assistant
    ok, detail = home_assistant.call_service("notify", service, service_data={"title": title[:120], "message": message[:1800]})
    return ok, "sent" if ok else str(detail)[:200]


def configured() -> bool:
    """True when at least one channel could deliver."""
    return app_push_status()[0] or bool(ha_service())


def send(title: str, message: str) -> Delivery:
    """Deliver to the phone through the first working channel."""
    message = (message or "").strip()
    if not message:
        return Delivery(False, None, "empty message")
    failures: list[str] = []
    ready, reason = app_push_status()
    if ready:
        ok, detail = _send_apns(title, message)
        if ok:
            return Delivery(True, "app_push", detail)
        failures.append(f"app push: {detail}")
        log.warning("Companion push failed, falling back to Home Assistant: %s", detail)
    try:
        ok, detail = _send_home_assistant(title, message)
    except Exception as exc:  # noqa: BLE001 -- a notifier must never kill a monitor
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    if ok:
        return Delivery(True, "home_assistant", detail)
    failures.append(f"home assistant: {detail}")
    if not ready:
        failures.append(f"app push skipped: {reason}")
    return Delivery(False, None, "; ".join(failures))


def status() -> dict[str, object]:
    ready, reason = app_push_status()
    return {
        "primary": "app_push" if ready else ("home_assistant" if ha_service() else None),
        "app_push": {"ready": ready, "reason": reason, "devices": device_count()},
        "home_assistant": {"configured": bool(ha_service()), "service": ha_service() or None},
    }
