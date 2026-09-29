"""Outbound phone notifications: JARVIS companion push first, HA as fallback.

Every proactive alert that should reach Nick's phone goes through ``send()``.
Channels are tried in order and the first successful one wins:

1. ``app_push`` -- APNs push to the JarvisCompanion iOS app. The app
   registers its device token through ``POST /api/push/register`` (stored in
   ``memory/push_devices.json``). Sending uses token-based auth: an ES256 JWT
   signed with the APNs auth key (.p8), sent over HTTP/2 to Apple. Ready once
   ``APNS_ENABLED=true``, ``APNS_KEY_PATH`` / ``APNS_KEY_ID`` /
   ``APNS_TEAM_ID`` / ``APNS_BUNDLE_ID`` are set, the key parses, and at
   least one device has registered. ``app_push_status()`` names the first
   missing piece.
2. ``home_assistant`` -- HA ``notify.mobile_app_*`` (the existing, working
   path). Service name from ``JARVIS_NOTIFY_HA_SERVICE``, falling back to the
   older ``ARR_STACK_NOTIFY_SERVICE``. Used only when companion push is not
   ready or every device failed.

Settings are read live on every call, like the rest of the project.
"""
from __future__ import annotations

import base64
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
_APNS_HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
# Apple rejects provider tokens older than 60 min and throttles refreshes
# more often than every 20 min; 50 min sits safely between.
_JWT_MAX_AGE_S = 50 * 60
_apns_lock = threading.Lock()
_jwt_cache: dict[str, object] = {"token": "", "issued": 0.0, "fingerprint": ""}
_http_client = None


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

def _apns_config() -> dict[str, str]:
    return {name: os.getenv(f"APNS_{name}", "").strip() for name in ("KEY_PATH", "KEY_ID", "TEAM_ID", "BUNDLE_ID")}


def app_push_status() -> tuple[bool, str]:
    """Whether companion push can be used, and if not, the first missing piece."""
    if os.getenv("APNS_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "APNS_ENABLED is off"
    config = _apns_config()
    missing = [f"APNS_{name}" for name, value in config.items() if not value]
    if missing:
        return False, "Missing " + ", ".join(missing)
    try:
        _load_signing_key(config["KEY_PATH"])
    except (OSError, ValueError, TypeError) as exc:
        return False, f"APNS_KEY_PATH is not a readable .p8 key ({type(exc).__name__})"
    try:
        import h2  # noqa: F401 -- httpx needs it for http2=True
    except ImportError:
        return False, "The 'h2' package is not installed (pip install -r requirements.txt)"
    if device_count() == 0:
        return False, "No companion device has registered a push token"
    return True, "ready"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_signing_key(path: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("APNs keys are P-256 EC keys")
    return key


def _es256_jwt(key, key_id: str, team_id: str, issued_at: int) -> str:
    """Sign an APNs provider token (RFC 7518 ES256: raw r||s, not DER)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    header = _b64url(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
    claims = _b64url(json.dumps({"iss": team_id, "iat": issued_at}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _provider_token(config: dict[str, str], *, force: bool = False) -> str:
    fingerprint = f"{config['KEY_PATH']}|{config['KEY_ID']}|{config['TEAM_ID']}"
    now = time.time()
    if (not force and _jwt_cache["token"] and _jwt_cache["fingerprint"] == fingerprint
            and now - float(_jwt_cache["issued"]) < _JWT_MAX_AGE_S):
        return str(_jwt_cache["token"])
    token = _es256_jwt(_load_signing_key(config["KEY_PATH"]), config["KEY_ID"], config["TEAM_ID"], int(now))
    _jwt_cache.update(token=token, issued=now, fingerprint=fingerprint)
    return token


def _client():
    """One persistent HTTP/2 client: APNs wants connections reused."""
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.Client(http2=True, timeout=10.0)
    return _http_client


def _apns_post(environment: str, device: str, payload: bytes, headers: dict[str, str]) -> tuple[int, str]:
    import httpx
    try:
        response = _client().post(f"{_APNS_HOSTS[environment]}/3/device/{device}", content=payload, headers=headers)
    except httpx.HTTPError as exc:
        return 0, f"{type(exc).__name__}"
    if response.status_code == 200:
        return 200, ""
    try:
        reason = str(response.json().get("reason", ""))
    except ValueError:
        reason = ""
    return response.status_code, reason or response.text[:80]


def _send_apns(title: str, message: str, *, thread_id: str = "", collapse_id: str = "",
               passive: bool = False) -> tuple[bool, str]:
    """Push to every registered device; success if at least one accepted it.

    ``thread_id`` groups notifications on the phone; ``collapse_id`` makes a
    newer push replace the older one instead of stacking. ``passive`` lands
    quietly in Notification Center (no sound, no banner). All three are
    standard APNs fields iOS handles itself; the app needs no code for them."""
    config = _apns_config()
    aps: dict[str, object] = {"alert": {"title": title[:120], "body": message[:1500]}}
    if passive:
        aps["interruption-level"] = "passive"
    else:
        aps["sound"] = "default"
    if thread_id:
        aps["thread-id"] = thread_id[:64]
    body = {"aps": aps, "source": "jarvis"}
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with _apns_lock:
        with _lock:
            devices = _load_devices()
        token = _provider_token(config)
        delivered, failures = 0, []
        removed: set[str] = set()
        moved: dict[str, str] = {}
        for device, info in list(devices.items()):
            environment = info.get("environment") if info.get("environment") in _APNS_HOSTS else "production"
            headers = {
                "authorization": f"bearer {token}",
                "apns-topic": config["BUNDLE_ID"],
                "apns-push-type": "alert",
                "apns-priority": "5" if passive else "10",
                # Alerts older than six hours are stale; let APNs drop them.
                "apns-expiration": str(int(time.time()) + 6 * 3600),
            }
            if collapse_id:
                headers["apns-collapse-id"] = collapse_id[:64]
            status, reason = _apns_post(environment, device, payload, headers)
            if status == 403 and reason == "ExpiredProviderToken":
                token = _provider_token(config, force=True)
                headers["authorization"] = f"bearer {token}"
                status, reason = _apns_post(environment, device, payload, headers)
            if status == 400 and reason == "BadDeviceToken":
                # Token/environment mismatch (a sandbox build's token sent to
                # production or vice versa): try the other host once and
                # remember whichever works.
                other = "sandbox" if environment == "production" else "production"
                status, reason = _apns_post(other, device, payload, headers)
                if status == 200:
                    moved[device] = other
            if status == 200:
                delivered += 1
                continue
            if status == 410 or reason in {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}:
                removed.add(device)
                log.info("Dropped APNs device %s…: %s", device[:8], reason or status)
            failures.append(f"{device[:8]}… {status} {reason}".strip())
        if removed or moved:
            # Re-read under the registry lock so a registration that arrived
            # during the sends is not overwritten.
            with _lock:
                current = _load_devices()
                for device in removed:
                    current.pop(device, None)
                for device, environment in moved.items():
                    if device in current:
                        current[device]["environment"] = environment
                _save_devices(current)
    if delivered:
        return True, f"sent to {delivered} device(s)"
    return False, "; ".join(failures) or "no registered devices"


def ha_service() -> str:
    service = (os.getenv("JARVIS_NOTIFY_HA_SERVICE", "").strip() or os.getenv("ARR_STACK_NOTIFY_SERVICE", "").strip())
    service = service.removeprefix("notify.")
    return service if re.fullmatch(r"mobile_app_[a-z0-9_]+", service) else ""


def _send_home_assistant(title: str, message: str, *, thread_id: str = "", collapse_id: str = "",
                         passive: bool = False) -> tuple[bool, str]:
    service = ha_service()
    if not service:
        return False, "No HA mobile_app notify service configured"
    from integrations import home_assistant
    service_data: dict[str, object] = {"title": title[:120], "message": message[:1800]}
    # HA companion equivalents: group -> thread-id, tag -> collapse-id.
    data: dict[str, object] = {}
    if thread_id:
        data["group"] = thread_id[:64]
    if collapse_id:
        data["tag"] = collapse_id[:64]
    if passive:
        data["push"] = {"interruption-level": "passive"}
    if data:
        service_data["data"] = data
    ok, detail = home_assistant.call_service("notify", service, service_data=service_data)
    return ok, "sent" if ok else str(detail)[:200]


def configured() -> bool:
    """True when at least one channel could deliver."""
    return app_push_status()[0] or bool(ha_service())


def send(title: str, message: str, *, thread_id: str = "", collapse_id: str = "",
         passive: bool = False) -> Delivery:
    """Deliver to the phone through the first working channel."""
    grouping = {k: v for k, v in (("thread_id", thread_id), ("collapse_id", collapse_id), ("passive", passive)) if v}
    message = (message or "").strip()
    if not message:
        return Delivery(False, None, "empty message")
    failures: list[str] = []
    ready, reason = app_push_status()
    if ready:
        try:
            ok, detail = _send_apns(title, message, **grouping)
        except Exception as exc:  # noqa: BLE001 -- fall back to HA, never raise
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        if ok:
            return Delivery(True, "app_push", detail)
        failures.append(f"app push: {detail}")
        log.warning("Companion push failed, falling back to Home Assistant: %s", detail)
    try:
        ok, detail = _send_home_assistant(title, message, **grouping)
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
