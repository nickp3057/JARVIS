"""Autopilot: JARVIS runs the basic stack fixes himself.

Nick's rule changed on 2026-11-20: "I'd also like jarvis to try to fix these
errors with some of just the basic stuff. Like restarting the arr stack ...
I don't want to have to manually do anything. The only thing I'd like to not
be automatic is the plex so if I'm actively watching something I'm not
interrupted." (The 2026-10-27 rule was "never automatically".)

So a fix from ``brain.stack_fixes`` may now run with ``approved_by="auto"``
when, and only when, every check here passes:

* ``AUTO_REPAIR_ENABLED`` is on (default on). Nick can say "turn autopilot off".
* The fix is in ``AUTO_REPAIR_FIXES`` and is never a Plex fix: ``NEVER_AUTO``
  is hard-coded and no setting can unlock it.
* Fixes that could disturb a film (a Home Assistant restart drops the light
  sync) wait while anything is playing on Plex.
* Cooldowns and a daily budget stop a broken service from being restarted
  in a loop: one arr recovery per ``AUTO_REPAIR_ARR_COOLDOWN_S`` (2 h), at most
  ``AUTO_REPAIR_ARR_DAILY_MAX`` (3) a day, ``AUTO_REPAIR_DAILY_MAX`` (12) fixes
  a day in all, and each (fix, target) pair once per cooldown.

Every automatic run is still a normal repair proposal in
``memory/repair_proposals.json`` (``approved_by: auto``), reports its verified
outcome as an alert, and shows in the app's Fixes card with an Auto badge.
The model can't trigger any of this: only monitors call ``consider_*`` and
``propose_stack_fix(..., auto=True)``.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "auto_repairs.json"
_LOCK = RLock()

# Never automatic, whatever the settings say (Nick, 2026-11-20).
NEVER_AUTO = frozenset({"refresh_plex"})
DEFAULT_FIXES = ("arr_recovery", "retest_indexers", "switch_indexer_mirror", "retry_download",
                 "restart_home_assistant")
# Fixes that wait while Plex is playing (HA restart stops the light sync mid-film).
_WAIT_FOR_PLEX = frozenset({"restart_home_assistant"})
# Arr keys a gluetun + dependents recreate is the cure for. Indexer backoff
# and Radarr/Sonarr's own advisories are not.
_STACK_KEYS = ("qbittorrent-api", "radarr-api", "sonarr-api", "prowlarr-api", "prowlarr-health",
               "qbittorrent-connection", "dht-zero")
# The NAS DHT watchdog owns these; it gets its turn first.
_WATCHDOG_OWNED = frozenset({"dht-zero", "qbittorrent-connection"})


# --- settings -----------------------------------------------------------------

def _flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _seconds(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, "") or default))
    except ValueError:
        return default


def _count(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def enabled() -> bool:
    return _flag("AUTO_REPAIR_ENABLED", True)


def allowed_fixes() -> set[str]:
    raw = (os.getenv("AUTO_REPAIR_FIXES", "") or "").strip()
    names = [n.strip().lower() for n in raw.split(",")] if raw else list(DEFAULT_FIXES)
    return {n for n in names if n} - NEVER_AUTO


def permitted(name: str) -> bool:
    """May this fix ever run without Nick's tap? (No budget check.)"""
    return enabled() and name in allowed_fixes() and name not in NEVER_AUTO


# --- run log (cooldowns and budget) ---------------------------------------------

def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _save(runs: list[dict[str, Any]]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(runs[-200:], indent=1), encoding="utf-8")
        tmp.replace(_FILE)
    except OSError as exc:
        log.warning("Could not save the autopilot log: %s", exc)


def _cooldown(name: str) -> float:
    if name == "arr_recovery":
        return _seconds("AUTO_REPAIR_ARR_COOLDOWN_S", 7200, 600)
    if name == "switch_indexer_mirror":
        return 12 * 3600
    if name == "retry_download":
        return 24 * 3600
    return _seconds("AUTO_REPAIR_COOLDOWN_S", 3600, 300)


def _plex_busy() -> bool | None:
    """True when something is playing (or paused mid-film) on Plex; None when unknown."""
    try:
        from integrations import plex
        if not plex.is_configured():
            return False
        ok, sessions = plex.sessions()
    except Exception:  # noqa: BLE001 - an unknown answer is handled by the caller
        return None
    if not ok or not isinstance(sessions, list):
        return None
    return bool(sessions)


def blocked_reason(name: str, key: str = "", now: float | None = None) -> str | None:
    """Why this fix can't run on its own right now, or None when it may."""
    now = time.time() if now is None else now
    if not enabled():
        return "autopilot is off"
    if name in NEVER_AUTO:
        return "Plex fixes are never automatic"
    if name not in allowed_fixes():
        return f"{name} isn't on the autopilot list"
    with _LOCK:
        runs = [r for r in _load() if now - float(r.get("at") or 0) < 86400]
    if len(runs) >= _count("AUTO_REPAIR_DAILY_MAX", 12):
        return "the daily autopilot budget is spent"
    same = [r for r in runs if r.get("fix") == name]
    if name == "arr_recovery" and len(same) >= _count("AUTO_REPAIR_ARR_DAILY_MAX", 3):
        return "the arr stack was already restarted automatically too often today"
    last = max((float(r.get("at") or 0) for r in same if r.get("key", "") == key), default=0.0)
    if now - last < _cooldown(name):
        return f"it already ran {int((now - last) // 60)} min ago"
    if name in _WAIT_FOR_PLEX:
        busy = _plex_busy()
        if busy is None:
            return "Plex couldn't be checked, so it waits in case you're watching"
        if busy:
            return "something is playing on Plex"
    return None


def record(name: str, key: str, proposal_id: str, now: float | None = None) -> None:
    with _LOCK:
        runs = _load()
        runs.append({"fix": name, "key": key, "proposal": proposal_id, "at": time.time() if now is None else now})
        _save(runs)


def recent_runs(hours: float = 24) -> list[dict[str, Any]]:
    cutoff = time.time() - hours * 3600
    with _LOCK:
        return [r for r in _load() if float(r.get("at") or 0) >= cutoff]


# --- alert <-> repair links -----------------------------------------------------

def link_for(kind: str, context: dict[str, Any] | None) -> str | None:
    """The key that ties an alert to the repair that answers it."""
    context = context or {}
    if kind in {"download_stalled", "download_error"} and context.get("torrent_hash"):
        return "torrent:" + str(context["torrent_hash"]).lower()[:16]
    if kind == "arr_stack":
        return "arr_stack"
    return None


def fix_for(kind: str, context: dict[str, Any] | None) -> str | None:
    """Which fix the app's Fix it button would run for this alert (no network)."""
    if kind in {"download_stalled", "download_error"} and (context or {}).get("torrent_hash"):
        return "retry_download"
    if kind == "arr_stack":
        return "arr_recovery"
    return None


# --- triggers (called from monitors) --------------------------------------------

# (name, key, reason) -> when that hold was last logged. A monitor re-asks
# every minute; one line an hour per reason is enough (was 600+ a day).
_held_logged: dict[tuple[str, str, str], float] = {}
_HOLD_LOG_EVERY_S = 3600.0


def _log_hold(name: str, key: str, reason: str) -> None:
    now = time.time()
    marker = (name, key, reason)
    if now - _held_logged.get(marker, 0.0) < _HOLD_LOG_EVERY_S:
        return
    _held_logged[marker] = now
    log.info("Autopilot held %s%s: %s", name, f" ({key})" if key else "", reason)


def _stage(name: str, rationale: str, *, key: str = "", params: dict[str, Any] | None = None,
           link: str | None = None) -> str | None:
    from brain import self_repair
    reason = blocked_reason(name, key)
    if reason:
        _log_hold(name, key, reason)
        return None
    return self_repair.propose_stack_fix(name, rationale, None, params=params, key=key or None,
                                         link=link, auto=True)


def consider_arr(failing: dict[str, tuple[str, float]], *, maintenance: bool, hold_until: float = 0.0,
                 now: float | None = None) -> list[str]:
    """One arr monitor poll: start the fixes the failures call for. Returns
    what was started (for logs and tests)."""
    now = time.time() if now is None else now
    if maintenance or now < hold_until:
        return []  # a recovery is already running or settling
    started: list[str] = []
    stack_after = _seconds("AUTO_REPAIR_ARR_AFTER_S", 600, 60)
    watchdog_after = _seconds("ARR_STACK_DHT_ALERT_AFTER_S", 1500, 0) + 300
    stuck = [key for key, (_, first_seen) in failing.items()
             if key in _STACK_KEYS
             and now - first_seen >= (watchdog_after if key in _WATCHDOG_OWNED else stack_after)]
    if stuck:
        details = "; ".join(failing[k][0] for k in stuck)
        if _stage("arr_recovery", f"Autopilot: {details}", link="arr_stack"):
            started.append("arr_recovery")
        return started  # the recreate re-tests indexers too; let it settle first

    indexers = {k: v for k, v in failing.items() if k.startswith("indexer:")}
    retest_after = _seconds("AUTO_REPAIR_INDEXER_AFTER_S", 1800, 300)
    if any(now - first_seen >= retest_after for _, first_seen in indexers.values()):
        if _stage("retest_indexers", "Autopilot: " + "; ".join(d for d, _ in indexers.values())[:400],
                  link="arr_stack"):
            started.append("retest_indexers")
    mirror_after = _seconds("AUTO_REPAIR_MIRROR_AFTER_S", 7200, 600)
    for key, (detail, first_seen) in indexers.items():
        if now - first_seen < mirror_after:
            continue
        indexer_id = key.split(":", 1)[1]
        if not indexer_id.isdigit():
            continue
        name = detail.split(" is down", 1)[0].removeprefix("Indexer ").strip() or key
        if _stage("switch_indexer_mirror", f"Autopilot: {detail}", key=f"indexer-{indexer_id}",
                  params={"indexer_id": int(indexer_id), "label": name}, link="arr_stack"):
            started.append(f"switch_indexer_mirror:{indexer_id}")
    return started


# --- the app's Fix it button ----------------------------------------------------

def fix_alert(alert: Any) -> dict[str, Any]:
    """Nick tapped Fix it on one alert: the tap is the approval, so this runs
    straight away (cooldowns don't apply to him). {started, message, repair_id}."""
    from brain import self_repair
    name = fix_for(alert.kind, alert.context)
    link = link_for(alert.kind, alert.context)
    if name == "retry_download":
        from integrations import media_doctor
        torrent_hash = str(alert.context.get("torrent_hash") or "").lower()
        found = media_doctor.find_queue_record(torrent_hash)
        if found is None:
            return {"started": False, "message": "Radarr and Sonarr don't have this download any more, "
                    "so there's nothing to retry. It may have finished or been removed."}
        params = media_doctor.retry_params(*found)
        reason = f"Fix it tapped: {found[2]}"
        self_repair.propose_stack_fix(name, reason, None, params=params, key=torrent_hash[:16], link=link)
    elif name == "arr_recovery":
        from integrations import nas_stack
        issues = nas_stack.poll_health().get("issues") or {}
        stack = [k for k in issues if not k.startswith("indexer:") and not k.endswith("-health")]
        if not issues:
            from brain import event_bus
            event_bus.resolve([alert.id])
            return {"started": False, "resolved": True,
                    "message": "The arr stack checks out healthy now, so I've cleared this one."}
        if not stack:
            name = "retest_indexers"
        self_repair.propose_stack_fix(name, "Fix it tapped: " + "; ".join(issues.values())[:400], None, link=link)
    else:
        return {"started": False, "message": "There's no one-tap fix for this one. Ask JARVIS and he'll dig in."}
    proposal = self_repair.latest_for_link(link or "", time.time() - 60)
    if proposal is None:
        return {"started": False, "message": "I couldn't stage the fix."}
    message = self_repair.apply(proposal["id"], confirmed=True, approved_by="app")
    return {"started": " is running" in message, "message": message, "repair_id": proposal["id"]}
