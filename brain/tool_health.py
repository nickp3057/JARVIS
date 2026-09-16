"""Self-diagnosis: a per-tool health ledger so JARVIS notices its own failures.

Every ``execute_tool`` call is scored as a success or a failure (a raised
exception, a "Tool error" reply, or an unmistakable outage phrase in the
result).  Tools that keep failing are flagged as *degraded* and rendered into
the dynamic system block, so the model stops retrying a dead integration,
tells Nick which subsystem is down and what to check, and clears the flag
once the tool works again.  Nothing here calls the model; it is pure
bookkeeping backed by ``memory/tool_health.json``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "tool_health.json"
_LOCK = RLock()

# Consecutive failures before a tool counts as degraded.
DEGRADED_AFTER = 2
# Failures in this window also count, even with successes in between.
RECENT_WINDOW_SECONDS = 3600
RECENT_FAILURES_FOR_DEGRADED = 4
_MAX_RECENT = 12
_MAX_ERROR_CHARS = 160

# Phrases that mark a "successful" handler return as an outage in disguise.
_FAILURE_MARKERS = (
    "tool error",
    "not configured",
    "unreachable",
    "timed out",
    "connection refused",
    "connection error",
    "could not connect",
    "can't reach",
    "cannot reach",
    "failed to connect",
    "authentication failed",
    "unauthorized",
    "http 401",
    "http 403",
    "http 500",
    "http 502",
    "http 503",
)


def _path() -> Path:
    return _FILE


def _load() -> dict[str, dict[str, Any]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not load tool health ledger: %s", exc)
    return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not save tool health ledger: %s", exc)


def looks_like_failure(result: str | None) -> bool:
    """Heuristic: does a handler's text reply describe an outage?"""
    text = (result or "").strip().lower()
    if not text:
        return False
    head = text[:200]
    return any(marker in head for marker in _FAILURE_MARKERS)


def record(name: str, *, ok: bool, error: str = "", elapsed_ms: float = 0.0) -> None:
    """Score one tool call.  Cheap; safe to call from any thread."""
    now = time.time()
    with _LOCK:
        data = _load()
        entry = data.get(name) or {
            "calls": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "last_ok_at": None,
            "last_error_at": None,
            "last_error": "",
            "recent_failures": [],
            "avg_ms": 0.0,
        }
        entry["calls"] = int(entry.get("calls", 0)) + 1
        prev_avg = float(entry.get("avg_ms", 0.0) or 0.0)
        entry["avg_ms"] = round(prev_avg + (float(elapsed_ms) - prev_avg) / entry["calls"], 1)
        recent = [t for t in entry.get("recent_failures", []) if now - float(t) <= RECENT_WINDOW_SECONDS]
        if ok:
            was_degraded = _is_degraded(entry, now)
            entry["consecutive_failures"] = 0
            entry["last_ok_at"] = now
            entry["recent_failures"] = recent
            if was_degraded and not _is_degraded(entry, now):
                log.info("Tool %s recovered.", name)
        else:
            entry["failures"] = int(entry.get("failures", 0)) + 1
            entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
            entry["last_error_at"] = now
            entry["last_error"] = " ".join((error or "").split())[:_MAX_ERROR_CHARS]
            recent.append(now)
            entry["recent_failures"] = recent[-_MAX_RECENT:]
        data[name] = entry
        _save(data)


def _is_degraded(entry: dict[str, Any], now: float | None = None) -> bool:
    now = now or time.time()
    if int(entry.get("consecutive_failures", 0)) >= DEGRADED_AFTER:
        return True
    recent = [t for t in entry.get("recent_failures", []) if now - float(t) <= RECENT_WINDOW_SECONDS]
    return len(recent) >= RECENT_FAILURES_FOR_DEGRADED


def degraded_tools() -> list[dict[str, Any]]:
    """Tools currently flagged as unhealthy, worst first."""
    now = time.time()
    with _LOCK:
        data = _load()
    out = []
    for name, entry in data.items():
        if _is_degraded(entry, now):
            out.append({"name": name, **entry})
    out.sort(key=lambda e: (-int(e.get("consecutive_failures", 0)), e["name"]))
    return out


def clear(name: str | None = None) -> str:
    """Forget failures for one tool (or all), e.g. after Nick fixes the cause."""
    with _LOCK:
        data = _load()
        if name is None:
            data = {}
            _save(data)
            return "Tool health ledger cleared, sir. Every subsystem is presumed healthy until proven otherwise."
        if name not in data:
            return f"No health record for '{name}', sir."
        data[name]["consecutive_failures"] = 0
        data[name]["recent_failures"] = []
        data[name]["last_error"] = ""
        _save(data)
    return f"Cleared the fault flag on {name}, sir. I'll trust it again until it fails."


def _ago(ts: Any) -> str:
    if not ts:
        return "never"
    delta = max(0, int(time.time() - float(ts)))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def render_block() -> str:
    """Prompt text for degraded tools only; "" when everything is healthy."""
    lines = []
    for entry in degraded_tools():
        err = entry.get("last_error") or "no error text"
        lines.append(
            f"- {entry['name']}: {int(entry.get('consecutive_failures', 0))} failures in a row, "
            f"last {_ago(entry.get('last_error_at'))}: {err}"
        )
    return "\n".join(lines)


def report() -> str:
    """Spoken-friendly summary of every tracked tool for the system_health tool."""
    with _LOCK:
        data = _load()
    if not data:
        return "No tool activity recorded yet, sir. Nothing has had the chance to fail."
    degraded = degraded_tools()
    total_calls = sum(int(e.get("calls", 0)) for e in data.values())
    total_fail = sum(int(e.get("failures", 0)) for e in data.values())
    lines = [f"{len(data)} tools used, {total_calls} calls, {total_fail} failures overall."]
    if degraded:
        lines.append("Degraded right now:")
        for entry in degraded:
            lines.append(
                f"  - {entry['name']}: {int(entry.get('consecutive_failures', 0))} consecutive failures, "
                f"last error {_ago(entry.get('last_error_at'))}: {entry.get('last_error') or 'no detail'}"
            )
    else:
        lines.append("Nothing is degraded; all subsystems responding.")
    noisy = sorted(
        (e for e in data.items() if int(e[1].get("failures", 0)) and e[0] not in {d["name"] for d in degraded}),
        key=lambda kv: -int(kv[1].get("failures", 0)),
    )[:5]
    if noisy:
        lines.append("Occasional faults (recovered):")
        for name, entry in noisy:
            lines.append(
                f"  - {name}: {int(entry.get('failures', 0))}/{int(entry.get('calls', 0))} failed, "
                f"last ok {_ago(entry.get('last_ok_at'))}"
            )
    slowest = sorted(data.items(), key=lambda kv: -float(kv[1].get("avg_ms", 0) or 0))[:3]
    slow = [f"{n} {float(e.get('avg_ms', 0)):.0f}ms" for n, e in slowest if float(e.get("avg_ms", 0) or 0) >= 1000]
    if slow:
        lines.append("Slowest on average: " + ", ".join(slow) + ".")
    return "\n".join(lines)
