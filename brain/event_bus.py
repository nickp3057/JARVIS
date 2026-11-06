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
import hashlib
import json
import os
from pathlib import Path
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
# The same warning raised again within ALERT_REPEAT_WINDOW_S (30 min) is
# delivered quietly, even across a jarvis-api restart (monitors that remember what they
# already sent in memory re-raised everything on every deploy). Only the
# duplicate phone push is suppressed; tests repoint this persisted state.
_REPEATS_FILE = Path(__file__).resolve().parents[1] / "memory" / "alert_repeats.json"
_repeats: dict[str, float] | None = None
# Alerts up to this id were cleared by Nick ("clear the needs-you list"):
# they leave the Hub's Needs attention card but stay in history for replay.
_cleared_through = 0
# Alerts the Refresh button found fixed (brain/alert_checks.py).
_resolved: set[int] = set()


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


def condense_key(kind: str, title: str, message: str, context: dict[str, Any] | None = None) -> str:
    """Alerts with the same key are one problem: the phone replaces the
    earlier push and the Needs attention list shows one row. What it's about
    (context, e.g. a torrent hash) when known, else the exact text."""
    about = json.dumps(context, sort_keys=True, default=str) if context else (message or "").strip()
    return hashlib.sha1(f"{kind}|{title}|{about}".encode()).hexdigest()[:20]


def _repeat_window() -> float:
    try:
        return max(0.0, float(os.getenv("ALERT_REPEAT_WINDOW_S", "1800")))
    except ValueError:
        return 1800.0


def _load_repeats() -> dict[str, float]:
    global _repeats
    if _repeats is None:
        try:
            _repeats = {str(k): float(v) for k, v in json.loads(_REPEATS_FILE.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            _repeats = {}
    return _repeats


def _save_repeats(snapshot: dict[str, float]) -> None:
    try:
        _REPEATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REPEATS_FILE.write_text(json.dumps(snapshot))
    except OSError:
        log.debug("Could not save alert repeats", exc_info=True)


def publish(kind: str, title: str, message: str, *, source: str, severity: str = "warning",
            push: bool = True, dedupe_key: str | None = None, cooldown_s: float = 0.0,
            context: dict[str, Any] | None = None, **grouping: Any) -> Alert | None:
    """Publish an alert; explicit publisher cooldowns may return None.

    The global repeat window suppresses phone pushes only. History and live
    clients must still see a problem, including after a process restart.
    """
    global _next_id
    severity = severity if severity in SEVERITIES else "warning"
    now = time.time()
    key = condense_key(kind, title, (message or "").strip(), context)
    if push:
        grouping.setdefault("collapse_id", f"a-{key}")
        grouping.setdefault("thread_id", kind)
    snapshot = None
    quiet_repeat = False
    with _lock:
        if dedupe_key and cooldown_s > 0 and now - _last_sent.get(dedupe_key, -1e18) < cooldown_s:
            return None
        if dedupe_key:
            _last_sent[dedupe_key] = now
        window = _repeat_window()
        if severity != "info" and window > 0:
            repeats = _load_repeats()
            matching = [a for a in _history
                        if condense_key(a.kind, a.title, a.message, a.context) == key]
            active = [a for a in matching if not is_cleared(a)]
            recurrence = bool(matching) and not active
            escalated = bool(active) and SEVERITIES.index(severity) > max(
                SEVERITIES.index(a.severity) for a in active)
            if now - repeats.get(key, -1e18) < window and not recurrence and not escalated:
                log.info("Alert repeat kept quietly [%s] %s", kind, title)
                push = False
                # Already-live clients have this condition. Do not show the
                # TV banner again; retain history for polling/reconnection.
                quiet_repeat = bool(active)
            else:
                repeats[key] = now
            for old in [k for k, t in repeats.items() if now - t >= window]:
                repeats.pop(old)
            snapshot = dict(repeats)
        alert = Alert(_next_id, kind, severity, title, (message or "").strip(), source, now, push,
                      {k: v for k, v in grouping.items() if v}, dict(context or {}))
        _next_id += 1
        _history.append(alert)
        subscribers = [] if quiet_repeat else list(_subscribers.items())
    if snapshot is not None:
        _save_repeats(snapshot)
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


def resolve(ids: list[int]) -> None:
    """Hide these alerts from the Needs attention list: they're fixed."""
    with _lock:
        _resolved.update(ids)


def is_cleared(alert: Alert) -> bool:
    """Cleared by Nick, or found fixed by a refresh."""
    return alert.id <= _cleared_through or alert.id in _resolved


def reset_for_tests() -> None:
    global _next_id, _cleared_through, _repeats
    with _lock:
        _next_id = 1
        _cleared_through = 0
        _repeats = {}
        _resolved.clear()
        _history.clear()
        _last_sent.clear()
        subscribers = list(_subscribers.values())
        _subscribers.clear()
    for subscriber in subscribers:
        if isinstance(subscriber, _BufferedSubscriber):
            subscriber.close()
