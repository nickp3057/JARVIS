"""Runtime arr monitoring state and session-bound, human-only recovery approval."""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
import uuid

from integrations import nas_stack

_ALERT_FILE = Path(__file__).resolve().parents[1] / 'memory' / 'arr_stack_alerts.json'
_pending: dict[str, tuple[float, str, str]] = {}
_pending_lock = threading.Lock()
_restart_lock = threading.Lock()
_WARNING = 'Recreating gluetun kills active downloads and takes about 3 minutes to settle; verification can take another 5 minutes.'


class AlertTracker:
    def __init__(self, path: Path | None = None):
        self.path = path or _ALERT_FILE
        self.zeros = 0
        try:
            data = json.loads(self.path.read_text())
            self.last = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
        except (OSError, ValueError, AttributeError):
            self.last = {}

    def notices(self, snapshot: dict, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        nodes = snapshot.get('dht_nodes')
        self.zeros = self.zeros + 1 if type(nodes) is int and nodes == 0 else 0
        issues = dict(snapshot['issues'])
        if self.zeros >= 3:
            issues['dht-zero'] = 'qBittorrent DHT has been zero for 3 consecutive checks'
        cooldown = nas_stack.seconds('ARR_STACK_ALERT_COOLDOWN_S', 1800, 60)
        notices = []
        for key, detail in issues.items():
            previous = self.last.get(key)
            if previous is None or now - previous >= cooldown or now < previous:
                notices.append(detail)
                self.last[key] = now
        if notices:
            self.last = {k: v for k, v in self.last.items() if now - v < 86400}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.last))
            temporary.replace(self.path)
        return notices


def prepare_restart(session_id: str | None) -> str:
    if not session_id:
        return 'Restart refused: a conversation session is required, sir.'
    if not nas_stack.enabled('ARR_STACK_RESTART_ENABLED'):
        return 'Arr stack restart is disabled until the restricted NAS keys are installed and tested, sir.'
    request_id = str(uuid.uuid4())
    token = uuid.uuid4().hex[:8]
    now = time.monotonic()
    with _pending_lock:
        for key, value in list(_pending.items()):
            if now - value[0] > 600:
                _pending.pop(key, None)
        _pending[session_id] = (now, token, request_id)
    return f'{_WARNING} Confirm by replying exactly: confirm restart arr stack {token}'


def conversation_action(text: str, session_id: str) -> str | None:
    """Runs only on original user text at the agent boundary, never tool args."""
    normalized = ' '.join(text.lower().strip().split()).rstrip('.!?')
    if normalized in {'restart arr stack', 'restart the arr stack', 'please restart arr stack', 'please restart the arr stack', 'can you restart the arr stack', 'can you restart arr stack'}:
        return prepare_restart(session_id)
    if normalized in {'cancel arr restart', 'cancel restart arr stack'}:
        with _pending_lock:
            _pending.pop(session_id, None)
        return 'Arr stack restart cancelled, sir.'
    match = re.fullmatch(r'confirm restart arr stack ([a-f0-9]{8})', normalized)
    if not match:
        return None
    with _pending_lock:
        proposal = _pending.get(session_id)
        if not proposal or time.monotonic() - proposal[0] > 600 or match[1] != proposal[1]:
            return 'That restart confirmation is invalid or expired, sir. Ask to restart the arr stack again.'
        # Consume before any network side effect, including ambiguous failures.
        _pending.pop(session_id, None)
    if not nas_stack.enabled('ARR_STACK_RESTART_ENABLED'):
        return 'Arr stack restart is now disabled; no restart was requested, sir.'
    if not _restart_lock.acquire(blocking=False):
        return 'An arr recovery request is already being monitored, sir. No second restart was requested.'
    try:
        return _run_recovery(proposal[2])
    finally:
        _restart_lock.release()


def _run_recovery(request_id: str) -> str:
    # Never retry the launch: a timeout might mean the detached job has started.
    ok, response = nas_stack.start_recovery(request_id)
    accepted = ok and isinstance(response, dict) and response.get('request_id') == request_id and response.get('state') in {'accepted', 'already_started'}
    deadline = time.monotonic() + (600 if accepted else 40)
    while time.monotonic() < deadline:
        ok, payload = nas_stack.watchdog_status()
        recovery = payload.get('recovery') if ok and isinstance(payload, dict) else None
        if isinstance(recovery, dict) and recovery.get('request_id') == request_id:
            if recovery.get('state') == 'failed':
                return f"Arr recovery failed at {recovery.get('phase', 'unknown phase')} (exit {recovery.get('exit_code', 'unknown')}), sir. Post-check DHT: {recovery.get('dht_nodes', 'unknown')}. Request {request_id}."
            if recovery.get('state') == 'success':
                nodes = recovery.get('dht_nodes')
                if recovery.get('gluetun_health') != 'healthy' or type(nodes) is not int or nodes <= 0:
                    return f'NAS returned an inconsistent success result, sir. Recovery is unverified. Request {request_id}.'
                live_ok, info = nas_stack.network_status()
                if live_ok and type(info.get('dht_nodes')) is int and info['dht_nodes'] > 0:
                    return f"Arr stack recovered, sir. Gluetun is healthy; NAS post-check DHT: {nodes}, current DHT from the Optiplex: {info['dht_nodes']}."
                return f'NAS verified recovery with healthy gluetun and DHT {nodes}, sir, but the Optiplex post-check could not confirm positive DHT. Please inspect the connection. Request {request_id}.'
            if recovery.get('state') == 'running':
                # Even if the launch reply was lost, a correlated running state
                # proves the job started; allow the full verification window.
                if not accepted:
                    accepted = True
                    deadline = time.monotonic() + 600
        time.sleep(5)
    return f'Arr recovery outcome is unknown, sir; no automatic retry was sent. Check NAS recovery status for request {request_id} before requesting another restart.'


def describe_status() -> str:
    snapshot = nas_stack.poll_health()
    issues = '; '.join(snapshot['issues'].values()) or 'All configured APIs are reachable with no health errors'
    return f"{issues}. DHT: {snapshot['dht_nodes']}; connection: {snapshot['connection_status']}. {nas_stack.watchdog_note()}"
