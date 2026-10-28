"""Governed self-repair: diagnose a degraded tool, propose a fix, apply on approval.

This is the acting half of self-diagnosis (`brain/tool_health.py` is the
sensing half).  Nothing here rewrites source.  A repair is one of three
bounded actions, and every one is staged as a proposal that only executes
when the exact proposal id is applied with ``confirmed=True``:

* ``set_setting``   - write one key through ``brain.settings.update_setting``
                      (schema-validated), remembering the old value.
* ``run_command``   - run a command Nick pre-approved in ``.env`` under
                      ``SELF_REPAIR_COMMANDS`` (``name=shell command;...``).
                      JARVIS can only pick a name, never compose a command.
* ``clear_flag``    - drop the fault flag and retry (for transient faults).
* ``stack_fix``     - one allowlisted NAS-stack fix from ``brain.stack_fixes``
                      (HA restart, indexer re-test, Plex rescan, arr
                      recovery). These need Nick's own approval: his "yes" or
                      code through ``brain.confirm_gate``, or the companion's
                      Approve tap. The model's ``confirmed=True`` is not
                      enough, and they run in the background, reporting the
                      verified outcome through the notifier (an alert).

After a repair the subsystem is re-probed.  If it is still failing, a
``set_setting`` repair is rolled back automatically to the previous value.
State lives in ``memory/repair_proposals.json``.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import time
import uuid
from pathlib import Path
from threading import RLock, Thread
from typing import Any, Callable
from urllib.parse import urlparse

from brain import settings as settings_mod
from brain import tool_health
from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "repair_proposals.json"
_LOCK = RLock()
_MAX_PROPOSALS = 50
_PROBE_TIMEOUT_S = 3.0
_COMMAND_TIMEOUT_S = 90
# (title, detail, severity) -> None; the runtime points this at raise_alert.
_notifier: Callable[[str, str, str], None] | None = None

# Subsystem playbooks.  ``tools`` are prefixes/names from brain/tools.py;
# ``env`` are the settings the subsystem needs; ``url_env`` is probed by TCP;
# ``commands`` are the SELF_REPAIR_COMMANDS names worth suggesting.
_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "home_assistant": {
        "label": "Home Assistant",
        "tools": (
            "control_device", "list_devices", "home_status", "test_home_assistant",
            "activate_home_action", "list_home_services", "call_home_service",
            "list_automations", "inspect_automation", "draft_automation",
            "apply_automation_draft", "rollback_automation_change", "home_assistant_monitor",
        ),
        "env": ("HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN"),
        "url_env": "HOME_ASSISTANT_URL",
        "commands": ("restart_home_assistant",),
        "hint": "HA runs in Docker on the UGREEN NAS; a token change or container restart usually fixes it.",
    },
    "spotify": {
        "label": "Spotify",
        "tools": (
            "play_music", "pause_music", "resume_music", "skip_track", "previous_track",
            "current_track", "set_volume", "list_speakers", "transfer_playback",
        ),
        "env": ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"),
        "url_env": None,
        "host": "api.spotify.com",
        "commands": (),
        "hint": "Usually an expired OAuth token or no active device; ask Nick to open Spotify on a speaker.",
    },
    "radarr": {
        "label": "Radarr",
        "tools": ("find_movie", "add_movie", "list_quality_profiles"),
        "env": ("RADARR_URL", "RADARR_API_KEY"),
        "url_env": "RADARR_URL",
        "commands": ("restart_radarr",),
        "hint": "Radarr runs on the NAS.",
    },
    "sonarr": {
        "label": "Sonarr",
        "tools": ("find_series", "add_series"),
        "env": ("SONARR_URL", "SONARR_API_KEY"),
        "url_env": "SONARR_URL",
        "commands": ("restart_sonarr",),
        "hint": "Sonarr runs on the NAS.",
    },
    "media": {
        "label": "Media library (Radarr/Sonarr)",
        "tools": ("media_queue", "media_health", "media_history", "missing_media", "upcoming_media"),
        "env": ("RADARR_URL", "SONARR_URL"),
        "url_env": "RADARR_URL",
        "commands": ("restart_radarr", "restart_sonarr"),
        "hint": "Read-only views over Radarr and Sonarr on the NAS.",
    },
    "qbittorrent": {
        "label": "qBittorrent",
        "tools": ("download_status", "torrent_inventory", "manage_torrent", "test_qbittorrent"),
        "env": ("QBITTORRENT_URL",),
        "url_env": "QBITTORRENT_URL",
        "commands": ("restart_qbittorrent",),
        "hint": "qBittorrent runs on the NAS behind the VPN container; the VPN dropping takes it down too.",
    },
    "weather": {
        "label": "Weather (Open-Meteo)",
        "tools": ("get_weather", "morning_briefing"),
        "env": (),
        "url_env": None,
        "host": "api.open-meteo.com",
        "commands": (),
        "hint": "Public API; failures are almost always the internet link.",
    },
    "local_model": {
        "label": "Local model",
        "tools": (),
        "env": ("LOCAL_MODEL_URL",),
        "url_env": "LOCAL_MODEL_URL",
        "commands": ("restart_local_model",),
        "hint": "Ollama/LM Studio on the Optiplex.",
    },
}


# --- persistence --------------------------------------------------------------

def _path() -> Path:
    return _FILE


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [p for p in data if isinstance(p, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not load repair proposals: %s", exc)
    return []


def _save(items: list[dict[str, Any]]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items[-_MAX_PROPOSALS:], indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not save repair proposals: %s", exc)


# --- approved commands --------------------------------------------------------

def approved_commands() -> dict[str, str]:
    """Parse SELF_REPAIR_COMMANDS: ``name=command;name2=command2``."""
    raw = (os.getenv("SELF_REPAIR_COMMANDS", "") or "").strip()
    out: dict[str, str] = {}
    for chunk in raw.split(";"):
        if "=" not in chunk:
            continue
        name, command = chunk.split("=", 1)
        name, command = name.strip().lower(), command.strip()
        if name and command:
            out[name] = command
    return out


# --- probing ------------------------------------------------------------------

def subsystem_for(tool: str) -> str | None:
    tool = (tool or "").strip().lower()
    for key, book in _PLAYBOOKS.items():
        if tool in book["tools"]:
            return key
    if tool in _PLAYBOOKS:
        return tool
    return None


def _probe_host(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_S):
            return True, f"{host}:{port} reachable"
    except OSError as exc:
        return False, f"{host}:{port} unreachable ({exc.__class__.__name__})"


def probe(subsystem: str) -> dict[str, Any]:
    """Read-only checks: required settings present, endpoint reachable."""
    book = _PLAYBOOKS[subsystem]
    findings: list[str] = []
    missing = [key for key in book["env"] if not (os.getenv(key, "") or "").strip()]
    for key in missing:
        findings.append(f"{key} is not set in .env")
    reachable: bool | None = None
    target = ""
    url_env = book.get("url_env")
    if url_env:
        url = (os.getenv(url_env, "") or "").strip()
        if url:
            parsed = urlparse(url if "://" in url else f"http://{url}")
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            target = f"{host}:{port}"
            reachable, note = _probe_host(host, port)
            findings.append(note)
    elif book.get("host"):
        target = f"{book['host']}:443"
        reachable, note = _probe_host(book["host"], 443)
        findings.append(note)
    return {
        "subsystem": subsystem,
        "label": book["label"],
        "missing_settings": missing,
        "reachable": reachable,
        "target": target,
        "findings": findings,
        "hint": book["hint"],
    }


# --- proposals ----------------------------------------------------------------

def _new_proposal(subsystem: str, tool: str, action: dict[str, Any], rationale: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:8],
        "created_at": time.time(),
        "subsystem": subsystem,
        "tool": tool,
        "action": action,
        "rationale": rationale,
        "status": "pending",
        "result": "",
    }


def diagnose(tool: str) -> str:
    """Diagnose a tool's subsystem, stage one repair proposal, and describe both."""
    subsystem = subsystem_for(tool)
    if subsystem is None:
        return (
            f"I don't have a repair playbook for '{tool}', sir. "
            "I can only clear its fault flag and retry; say so if you'd like that."
        )
    book = _PLAYBOOKS[subsystem]
    result = probe(subsystem)
    commands = approved_commands()
    health = {e["name"]: e for e in tool_health.degraded_tools()}
    last_error = health.get(tool, {}).get("last_error", "")

    action: dict[str, Any]
    rationale: str
    if result["missing_settings"]:
        key = result["missing_settings"][0]
        editable = key in settings_mod.SETTINGS_SCHEMA
        action = {"kind": "set_setting", "key": key, "value": None}
        rationale = (
            f"{key} is missing. " + ("Give me the value and I'll write it to .env." if editable
            else "It's a secret, so Nick must add it to .env by hand; I'll only clear the flag afterwards.")
        )
        if not editable:
            action = {"kind": "clear_flag", "tool": tool}
    elif result["reachable"] is False:
        name = next((c for c in book["commands"] if c in commands), None)
        if name:
            action = {"kind": "run_command", "name": name}
            rationale = f"{book['label']} at {result['target']} is not answering; the approved '{name}' command should bring it back."
        else:
            action = {"kind": "clear_flag", "tool": tool}
            rationale = (
                f"{book['label']} at {result['target']} is not answering and no approved restart command "
                f"is configured (add one to SELF_REPAIR_COMMANDS as {book['commands'][0] if book['commands'] else 'restart_x'}=...). "
                "Nick needs to check the service; I'll retry once he says it's up."
            )
    else:
        action = {"kind": "clear_flag", "tool": tool}
        rationale = (
            f"{book['label']} looks reachable and configured, so the fault is probably transient or "
            "an auth/permission problem inside the service. Clearing the flag and retrying is the safe move."
        )

    proposal = _new_proposal(subsystem, tool, action, rationale)
    with _LOCK:
        items = _load()
        items = [p for p in items if not (p.get("status") == "pending" and p.get("tool") == tool)]
        items.append(proposal)
        _save(items)

    lines = [f"Diagnosis for {tool} ({book['label']}):"]
    if last_error:
        lines.append(f"- last error: {last_error}")
    lines.extend(f"- {f}" for f in result["findings"])
    lines.append(f"- note: {book['hint']}")
    lines.append(f"Proposed repair [{proposal['id']}]: {_describe_action(action)}. {rationale}")
    lines.append(f"Say the word and I'll apply repair {proposal['id']}, sir.")
    return "\n".join(lines)


def set_notifier(callback: Callable[[str, str, str], None] | None) -> None:
    global _notifier
    _notifier = callback


def propose_stack_fix(name: str, rationale: str, session_id: str | None = None, *,
                      params: dict[str, Any] | None = None, key: str | None = None) -> str:
    """Stage an allowlisted stack fix; in chat, open the confirm gate for it.

    Nothing runs here. The fix runs only when Nick replies "yes"/the code
    (confirm gate) or taps Approve in the app (``POST /api/repairs/{id}/apply``)."""
    from brain import confirm_gate, stack_fixes
    name = (name or "").strip().lower()
    fix = stack_fixes.FIXES.get(name)
    if fix is None:
        return f"'{name}' is not an allowlisted stack fix, sir. Options: {', '.join(stack_fixes.FIXES)}."
    # ``key`` scopes "one pending proposal" (e.g. one retry per torrent).
    tool = f"{name}:{key}" if key else name
    action = {"kind": "stack_fix", "name": name, **({"params": params} if params else {})}
    label = stack_fixes.describe(name, params)
    proposal = _new_proposal("nas_stack", tool, action, (rationale or "").strip()[:500] or label)
    with _LOCK:
        items = [p for p in _load() if not (p.get("status") == "pending" and p.get("tool") == tool)]
        items.append(proposal)
        _save(items)
    staged = f"Staged repair {proposal['id']}: {label}. It also shows in the app with an Approve button."
    if not session_id:
        return staged + " Nothing has been changed yet."
    return staged + " " + confirm_gate.propose(
        session_id, f"{label} [repair {proposal['id']}]",
        lambda: apply(proposal["id"], confirmed=True, approved_by="chat"),
    )


def public_proposals(hours: float = 24) -> list[dict[str, Any]]:
    """Pending repairs plus recently finished ones, newest first, for the app."""
    cutoff = time.time() - hours * 3600
    with _LOCK:
        items = _load()
    out = []
    for p in reversed(items):
        if p.get("status") not in {"pending", "running"} and float(p.get("finished_at") or p.get("created_at") or 0) < cutoff:
            continue
        out.append({"id": p["id"], "kind": p["action"].get("kind"), "target": p.get("tool"), "label": _describe_action(p["action"]),
                    "rationale": p.get("rationale", ""), "status": p.get("status"), "result": p.get("result", ""),
                    "created_at": p.get("created_at"), "finished_at": p.get("finished_at")})
    return out[:20]


def _describe_action(action: dict[str, Any]) -> str:
    kind = action.get("kind")
    if kind == "stack_fix":
        from brain import stack_fixes
        return stack_fixes.describe(str(action.get("name")), action.get("params"))
    if kind == "set_setting":
        return f"set {action.get('key')} in .env" + (f" to {action.get('value')!r}" if action.get("value") is not None else "")
    if kind == "run_command":
        return f"run approved command '{action.get('name')}'"
    return f"clear the fault flag on {action.get('tool')} and retry"


def list_proposals(status: str = "pending") -> str:
    with _LOCK:
        items = _load()
    wanted = [p for p in items if status == "all" or p.get("status") == status]
    if not wanted:
        return f"No {status} repair proposals, sir."
    lines = [f"{len(wanted)} {status} repair proposal(s):"]
    for p in wanted[-10:]:
        lines.append(f"- [{p['id']}] {p['tool']}: {_describe_action(p['action'])} ({p['status']}) {p.get('result', '')}".rstrip())
    return "\n".join(lines)


def _finish(proposal_id: str, status: str, outcome: str) -> None:
    with _LOCK:
        items = _load()
        for p in items:
            if p.get("id") == proposal_id:
                p["status"] = status
                p["result"] = outcome[:500]
                p["finished_at"] = time.time()
        _save(items)


def _run_stack_fix(proposal: dict[str, Any], approved_by: str) -> None:
    from brain import stack_fixes
    name = proposal["action"]["name"]
    ok, outcome = stack_fixes.run(name, proposal["action"].get("params"))
    _finish(proposal["id"], "applied" if ok else "failed", outcome)
    log.info("Stack fix %s (%s, approved via %s): %s", proposal["id"], name, approved_by, outcome)
    if _notifier is not None:
        title = f"Repair {'done' if ok else 'failed'}: {stack_fixes.describe(name, proposal['action'].get('params')).split(':')[0].split(',')[0]}"
        try:
            _notifier(title[:120], outcome, "info" if ok else "warning")
        except Exception:  # noqa: BLE001 - delivery problems must not hide the result
            log.exception("Repair notifier failed")


def apply(proposal_id: str, *, confirmed: bool = False, value: Any = None, approved_by: str | None = None) -> str:
    """Execute a staged repair.  Requires the exact id and confirmed=True.

    ``approved_by`` ("chat" from the confirm gate, "app" from the Approve tap)
    is required for stack fixes: they only ever follow a message or tap from
    Nick himself, so the model's apply_repair call alone is refused."""
    if not confirmed:
        return "I need Nick's explicit go-ahead before I apply a repair, sir."
    with _LOCK:
        items = _load()
        proposal = next((p for p in items if p.get("id") == (proposal_id or "").strip()), None)
        if proposal is None:
            return f"No repair proposal with id '{proposal_id}', sir."
        if proposal.get("status") != "pending":
            return f"Repair {proposal_id} was already {proposal.get('status')}."
        if proposal["action"].get("kind") == "stack_fix" and approved_by not in {"chat", "app"}:
            return (f"Repair {proposal_id} restarts or changes part of the NAS stack, so it needs Nick's own "
                    "approval: his \"yes\" or code in chat, or the Approve button in the app, sir.")
        action = proposal["action"]
        if action.get("kind") == "set_setting" and value is None and action.get("value") is None:
            # Ask before marking it running, or it could never be applied with the value.
            return f"Repair {proposal_id} needs a value for {action['key']}, sir. Tell me what to set it to."
        proposal["status"] = "running"
        _save(items)

    if proposal["action"]["kind"] == "stack_fix":
        Thread(target=_run_stack_fix, args=(proposal, approved_by), daemon=True,
               name=f"stack-fix-{proposal['id']}").start()
        return (f"Repair {proposal_id} is running: {_describe_action(proposal['action'])}. "
                "I'll report the verified result when it finishes, sir.")

    action = dict(proposal["action"])
    tool = proposal["tool"]
    subsystem = proposal["subsystem"]
    outcome = ""
    rolled_back = False
    try:
        if action["kind"] == "set_setting":
            key = action["key"]
            new_value = value if value is not None else action.get("value")
            old_value = os.getenv(key, "")
            outcome = settings_mod.update_setting(key, new_value)
            if not outcome.startswith(f"{key} set to"):
                raise RuntimeError(outcome)
            after = probe(subsystem)
            if after["reachable"] is False:
                still = f" Still unreachable afterwards ({after['findings'][-1]})"
                if old_value:
                    settings_mod.update_setting(key, old_value)
                    rolled_back = True
                    outcome += f"{still}, so I rolled {key} back."
                else:  # it was unset: nothing to go back to, so say so rather than claim a rollback
                    outcome += f"{still}; {key} was unset before, so I left the new value in place."
        elif action["kind"] == "run_command":
            command = approved_commands().get(action["name"])
            if not command:
                raise RuntimeError(f"'{action['name']}' is no longer in SELF_REPAIR_COMMANDS.")
            completed = subprocess.run(
                shlex.split(command) if not any(ch in command for ch in "|&;><") else ["/bin/sh", "-c", command],
                capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False,
            )
            tail = (completed.stdout or completed.stderr or "").strip()[-300:]
            outcome = f"'{action['name']}' exited {completed.returncode}. {tail}".strip()
            if completed.returncode != 0:
                raise RuntimeError(outcome)
            time.sleep(2)
            after = probe(subsystem)
            outcome += " Endpoint " + ("is back." if after["reachable"] else "is still not answering.")
        else:
            outcome = "Fault flag cleared."
        if not rolled_back:
            tool_health.clear(tool)
        status = "rolled_back" if rolled_back else "applied"
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        outcome = f"Repair failed: {exc}"
        status = "failed"
        log.warning("Self-repair %s failed: %s", proposal_id, exc)

    _finish(proposal["id"], status, outcome)
    verdict = {"applied": "Repair applied", "rolled_back": "Repair rolled back", "failed": "Repair failed"}[status]
    return f"{verdict} for {tool}, sir. {outcome}"


def reject(proposal_id: str) -> str:
    with _LOCK:
        items = _load()
        for p in items:
            if p.get("id") == (proposal_id or "").strip() and p.get("status") == "pending":
                p["status"] = "rejected"
                _save(items)
                return f"Repair {proposal_id} discarded, sir."
    return f"No pending repair proposal with id '{proposal_id}', sir."
