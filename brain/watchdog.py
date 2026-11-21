"""Watchdog: JARVIS notices his own background monitors dying or freezing.

Two layers (FUTURE_SESSIONS.md Tier 2 #8, built 2026-11-20):

* ``MonitorWatch`` — the runtime registers every monitor thread it starts
  (``JarvisRuntime._spawn``). Once a minute ``_watchdog_loop`` asks this class
  what to do: a thread that has *died* is restarted (at most
  ``MONITOR_RESTART_MAX`` (3) times an hour each, then it gives up and says
  so); a thread whose innermost frame hasn't moved for ``MONITOR_HANG_S``
  (15 min) while not waiting or sleeping is reported *stuck* once. A stuck
  Python thread can't be killed safely and a second copy could double-act
  (two arr monitors), so stuck threads are reported, not restarted; the
  process-level watchdog below covers a truly wedged Core.

* ``SystemdWatchdog`` — when systemd runs Core with ``WatchdogSec=`` (set up
  by ``deploy/surface-setup.sh``), it pings ``WATCHDOG=1`` only while Core's
  own ``/health`` answers. A hung server stops the pings and systemd restarts
  it. Without ``NOTIFY_SOCKET``/``WATCHDOG_USEC`` (dev, the Mac) it does nothing.

No model calls; outcomes go to ``tool_health`` (``monitor:<name>``) and the
runtime's alerts.
"""
from __future__ import annotations

import linecache
import os
import socket
import threading
import time
from typing import Any, Callable, NamedTuple

from jarvis_logger import get_logger

log = get_logger(__name__)

# Lines that legitimately block for a long time.
_IDLE_MARKERS = ("sleep(", ".wait(", "recv(", "serve_forever", ".get(timeout", "select(")


class Supervised(NamedTuple):
    target: Callable[[], None]
    thread: Any
    attr: str | None  # runtime attribute that mirrors the thread (e.g. _arr_monitor_thread)


class Action(NamedTuple):
    kind: str  # restart | gave_up | stuck | unstuck
    name: str
    detail: str = ""


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, "") or default))
    except ValueError:
        return default


def _location(frame: Any) -> tuple[str, int, str] | None:
    """The innermost frame, or None when the thread is idle (waiting/sleeping)."""
    innermost = frame
    walk = frame
    while walk is not None:
        if walk.f_code.co_filename.endswith("threading.py") and walk.f_code.co_name in {"wait", "_wait_for_tstate_lock"}:
            return None
        walk = walk.f_back
    line = linecache.getline(innermost.f_code.co_filename, innermost.f_lineno)
    if any(marker in line for marker in _IDLE_MARKERS):
        return None
    return innermost.f_code.co_filename, innermost.f_lineno, innermost.f_code.co_name


class MonitorWatch:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._restarts: dict[str, list[float]] = {}
        self._gave_up: set[str] = set()
        # name -> (location, since, reported)
        self._still: dict[str, tuple[tuple[str, int, str], float, bool]] = {}

    def check(self, supervised: dict[str, Supervised], frames: dict[int, Any]) -> list[Action]:
        now = self._clock()
        hang_s = _env_float("MONITOR_HANG_S", 900, 60)
        limit = int(_env_float("MONITOR_RESTART_MAX", 3, 0))
        actions: list[Action] = []
        for name, entry in supervised.items():
            thread = entry.thread
            alive = getattr(thread, "is_alive", lambda: True)()
            if not alive:
                self._still.pop(name, None)
                if name in self._gave_up:
                    continue
                recent = [t for t in self._restarts.get(name, []) if now - t < 3600]
                if len(recent) >= limit:
                    self._gave_up.add(name)
                    actions.append(Action("gave_up", name, f"died {len(recent) + 1} times in an hour"))
                    continue
                recent.append(now)
                self._restarts[name] = recent
                actions.append(Action("restart", name))
                continue
            self._gave_up.discard(name)
            frame = frames.get(getattr(thread, "ident", None) or -1)
            where = _location(frame) if frame is not None else None
            previous = self._still.get(name)
            if where is None:
                if previous and previous[2]:
                    actions.append(Action("unstuck", name))
                self._still.pop(name, None)
                continue
            if previous is None or previous[0] != where:
                if previous and previous[2]:
                    actions.append(Action("unstuck", name))
                self._still[name] = (where, now, False)
                continue
            if not previous[2] and now - previous[1] >= hang_s:
                self._still[name] = (where, previous[1], True)
                path, line, function = where
                actions.append(Action("stuck", name, f"{os.path.basename(path)}:{line} in {function}() "
                                                     f"for {int((now - previous[1]) // 60)} min"))
        return actions


# --- systemd watchdog ----------------------------------------------------------

def _notify(message: str) -> bool:
    address = os.getenv("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
        return True
    except OSError as exc:
        log.warning("systemd notify failed: %s", exc)
        return False


class SystemdWatchdog:
    """Pings systemd while ``healthy()`` says yes. Inert outside systemd."""

    def __init__(self, healthy: Callable[[], bool]) -> None:
        self._healthy = healthy
        self._stop = threading.Event()

    @staticmethod
    def interval() -> float | None:
        try:
            usec = int(os.getenv("WATCHDOG_USEC", "") or 0)
        except ValueError:
            return None
        if usec <= 0 or not os.getenv("NOTIFY_SOCKET"):
            return None
        return max(1.0, usec / 1_000_000 / 3)

    def start(self) -> bool:
        every = self.interval()
        if every is None:
            return False
        _notify("READY=1")
        threading.Thread(target=self._run, args=(every,), daemon=True).start()
        log.info("systemd watchdog on: pinging every %.0f s while /health answers", every)
        return True

    def _run(self, every: float) -> None:
        while not self._stop.wait(every):
            try:
                ok = self._healthy()
            except Exception:  # noqa: BLE001 - an exception is a failed check
                ok = False
            if ok:
                _notify("WATCHDOG=1")
            else:
                log.warning("Health check failed; withholding the systemd watchdog ping")

    def stop(self) -> None:
        self._stop.set()
