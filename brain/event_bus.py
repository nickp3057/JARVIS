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
Fast subscribers run synchronously. Network subscribers use a bounded FIFO
worker so phone timeouts cannot delay client streams or monitor loops.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import threading
import queue
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
# Alerts up to this id were cleared by Nick ("clear the needs-you list"):
# they leave the Hub's Needs attention card but stay in history for replay.
_cleared_through = 0


class _BufferedSubscriber:
    def __init__(self, name: str, callback: Subscriber, capacity: int = _HISTORY) -> None:
        self.name, self.callback = name, callback
        self.queue: queue.Queue[Alert] = queue.Queue(maxsize=capacity)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"alert-{name}")
        self.thread.start()

    def __call__(self, alert: Alert) -> None:
        if self.stopped.is_set():
            return
        try:
            self.queue.put_nowait(alert)
        except queue.Full:
            # Preserve bounded memory and publisher latency. The bus history
            # still exposes the alert to clients; surface the missed push.
            log.error("Alert subscriber %s queue full; push %s not queued (retained in bus history)", self.name, alert.id)

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                alert = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if not self.stopped.is_set():
                    self.callback(alert)
            except Exception:
                log.exception("Alert subscriber %s failed", self.name)
            finally:
                self.queue.task_done()

    def close(self) -> None:
        self.stopped.set()


def subscribe(name: str, callback: Subscriber, *, background: bool = False) -> None:
    """Register a fast callback or a bounded worker for network delivery."""
    subscriber = _BufferedSubscriber(name, callback) if background else callback
    with _lock:
        previous = _subscribers.get(name)
        _subscribers[name] = subscriber
    if isinstance(previous, _BufferedSubscriber):
        previous.close()


def unsubscribe(name: str) -> None:
    with _lock:
        previous = _subscribers.pop(name, None)
    if isinstance(previous, _BufferedSubscriber):
        previous.close()


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


def clear_attention() -> int:
    """Clear every warning/critical alert so far off the Needs attention list;
    returns how many were showing. New alerts still appear."""
    global _cleared_through
    with _lock:
        count = sum(1 for a in _history if a.id > _cleared_through and a.severity != "info")
        _cleared_through = _next_id - 1
    return count


def is_cleared(alert: Alert) -> bool:
    return alert.id <= _cleared_through


def reset_for_tests() -> None:
    global _next_id, _cleared_through
    with _lock:
        _next_id = 1
        _cleared_through = 0
        _history.clear()
        _last_sent.clear()
        subscribers = list(_subscribers.values())
        _subscribers.clear()
    for subscriber in subscribers:
        if isinstance(subscriber, _BufferedSubscriber):
            subscriber.close()
