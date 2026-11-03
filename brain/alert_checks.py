"""Is this alert still true? The Hub's Needs attention Refresh button.

``refresh()`` re-checks every alert on the Needs attention list (warning and
critical, last 24 h, not already cleared) against the live system and hides
the ones that have been fixed; the rest stay. Nick asked for this instead of
a Clear button (2026-11-03): "if it still needs me it'll stay but if it
doesn't then it disappears".

Each check answers True (still a problem), False (fixed) or None (can't
tell, so it stays). Kinds without a live check count as fixed when the same
source has since sent an info alert (the arr stack's and the NAS's
"recovered" notes, a device's "back online").
"""
from __future__ import annotations

import time
from typing import Any, Callable

from brain import event_bus
from jarvis_logger import get_logger

log = get_logger(__name__)

# The wall iPad's latest battery report ({device: (level, charging)}),
# written by POST /api/device/battery.
battery_reports: dict[str, tuple[float, bool]] = {}
LOW_BATTERY = 0.20


class _Live:
    """Per-refresh caches, so ten stalled-download alerts cost one qBittorrent call."""

    def __init__(self) -> None:
        self._torrents: dict[str, dict[str, Any]] | None = None
        self._states: dict[str, dict[str, Any]] | None = None

    def torrents(self) -> dict[str, dict[str, Any]] | None:
        if self._torrents is None:
            from integrations import qbittorrent
            ok, payload = qbittorrent.torrent_info("all")
            if not ok or not isinstance(payload, list):
                return None
            self._torrents = {str(t.get("hash", "")).lower(): t for t in payload}
        return self._torrents

    def states(self) -> dict[str, dict[str, Any]] | None:
        if self._states is None:
            from integrations.home_assistant import get_states
            ok, payload = get_states()
            if not ok or not isinstance(payload, list):
                return None
            self._states = {str(e.get("entity_id")): e for e in payload if isinstance(e, dict)}
        return self._states


def _schwab(alert: event_bus.Alert, live: _Live) -> bool | None:
    from integrations import schwab
    return not schwab.linked() or schwab.login_warning() is not None


def _torrent(alert: event_bus.Alert, live: _Live) -> bool | None:
    torrent_hash = str(alert.context.get("torrent_hash") or "").lower()
    torrents = live.torrents() if torrent_hash else None
    if torrents is None:
        return None
    item = torrents.get(torrent_hash)
    if item is None or float(item.get("progress", 0) or 0) >= 1:
        return False  # removed, or finished after all
    if alert.kind == "download_error":
        return "error" in str(item.get("state", "")).lower()
    from integrations.media_doctor import no_progress_stall
    return no_progress_stall(item, time.time()) is not None


def _device(alert: event_bus.Alert, live: _Live) -> bool | None:
    entity_id = str(alert.context.get("entity_id") or "")
    states = live.states() if entity_id else None
    if states is None:
        return None
    entity = states.get(entity_id)
    return entity is None or str(entity.get("state", "")).lower() in {"unavailable", "unknown"}


def _battery(alert: event_bus.Alert, live: _Live) -> bool | None:
    report = battery_reports.get(str(alert.context.get("device") or ""))
    if report is None:
        return None
    level, charging = report
    return level < LOW_BATTERY and not charging


CHECKS: dict[str, Callable[[event_bus.Alert, _Live], bool | None]] = {
    "schwab_login": _schwab,
    "download_stalled": _torrent,
    "download_error": _torrent,
    "device_offline": _device,
    "device_battery": _battery,
}


def _recovered_since(alert: event_bus.Alert, history: list[event_bus.Alert]) -> bool:
    return any(a.id > alert.id and a.source == alert.source and a.severity == "info" for a in history)


def needs_attention(hours: float = 24) -> list[event_bus.Alert]:
    cutoff = time.time() - hours * 3600
    return [a for a in event_bus.since(0, limit=200)
            if a.severity != "info" and a.created_at >= cutoff and not event_bus.is_cleared(a)]


def refresh(hours: float = 24) -> dict[str, int]:
    """Re-check the Needs attention list; hide what's fixed. {resolved, remaining}."""
    history = event_bus.since(0, limit=200)
    live, fixed, remaining = _Live(), [], 0
    for alert in needs_attention(hours):
        check = CHECKS.get(alert.kind)
        try:
            still = check(alert, live) if check else None
        except Exception:  # noqa: BLE001 - a broken check keeps the alert, never hides it
            log.warning("Alert check for %s failed", alert.kind, exc_info=True)
            still = None
        if still is None and _recovered_since(alert, history):
            still = False
        if still is False:
            fixed.append(alert.id)
        else:
            remaining += 1
    event_bus.resolve(fixed)
    return {"resolved": len(fixed), "remaining": remaining}
