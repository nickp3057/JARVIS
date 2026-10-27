"""One in-process bus for every proactive alert JARVIS raises.

Publishers (arr monitor, download monitor, device watch, recovery follow-up,
...) call ``publish``; they no longer know how alerts travel. Subscribers
decide delivery:

- the phone (``brain.notifications``: APNs companion push first, Home
  Assistant notify only as fallback), registered by the runtime;
- live client streams (``GET /api/clients/events`` server-sent events, for
  the TV and future clients), which replay recent alerts on reconnect via
  ``since(last_id)``.

Alerts are deduplicated by ``dedupe_key`` within ``cooldown_s`` so a monitor
that re-detects the same condition every poll doesn't spam every channel.
Subscribers run synchronously in the publisher's thread and must not raise;
slow ones (APNs) are already bounded by their own timeouts.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import threading
import time
from typing import Any, Callable

from jarvis_logger import get_logger

log = get_logger(__name__)

SEVERITIES = ("info", "warning", "critical")
_HISTORY = 200


@dataclass(frozen=True)
class Alert:
    id: int
    kind: str  # e.g. arr_stack, download_stalled, device_offline
    severity: str
    title: str
    message: str
    source: str
    created_at: float
    # Push only alerts that should buzz the phone; info still reaches streams.
    push: bool = True
    grouping: dict[str, Any] = field(default_factory=dict)  # thread_id / collapse_id / passive
    # What the alert is about, for tap-to-chat reports (e.g. torrent_hash).
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("grouping")
        return data


Subscriber = Callable[[Alert], Any]

_lock = threading.Lock()
_next_id = 1
_history: deque[Alert] = deque(maxlen=_HISTORY)
_last_sent: dict[str, float] = {}
_subscribers: dict[str, Subscriber] = {}


def subscribe(name: str, callback: Subscriber) -> None:
    """Register (or replace) a named subscriber."""
    with _lock:
        _subscribers[name] = callback


def unsubscribe(name: str) -> None:
    with _lock:
        _subscribers.pop(name, None)


def publish(kind: str, title: str, message: str, *, source: str, severity: str = "warning",
            push: bool = True, dedupe_key: str | None = None, cooldown_s: float = 0.0,
            context: dict[str, Any] | None = None, **grouping: Any) -> Alert | None:
    """Publish an alert to every subscriber; None if deduplicated."""
    global _next_id
    severity = severity if severity in SEVERITIES else "warning"
    now = time.time()
    with _lock:
        if dedupe_key and cooldown_s > 0 and now - _last_sent.get(dedupe_key, -1e18) < cooldown_s:
            return None
        if dedupe_key:
            _last_sent[dedupe_key] = now
        alert = Alert(_next_id, kind, severity, title, (message or "").strip(), source, now, push,
                      {k: v for k, v in grouping.items() if v}, dict(context or {}))
        _next_id += 1
        _history.append(alert)
        subscribers = list(_subscribers.items())
    log.info("Alert %s [%s/%s] %s", alert.id, kind, severity, title)
    for name, callback in subscribers:
        try:
            callback(alert)
        except Exception:  # noqa: BLE001 - one broken channel must not block the others
            log.exception("Alert subscriber %s failed", name)
    return alert


def since(last_id: int = 0, limit: int = 50) -> list[Alert]:
    """Alerts newer than ``last_id`` (oldest first), for stream replay."""
    with _lock:
        return [a for a in _history if a.id > last_id][-limit:]


def reset_for_tests() -> None:
    global _next_id
    with _lock:
        _next_id = 1
        _history.clear()
        _last_sent.clear()
        _subscribers.clear()
