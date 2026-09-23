"""Runtime arr monitoring state and session-bound, human-only recovery approval."""
from __future__ import annotations

import difflib
import json
from pathlib import Path
import re
import threading
import time
from typing import NamedTuple
import uuid

from integrations import nas_stack

_ALERT_FILE = Path(__file__).resolve().parents[1] / 'memory' / 'arr_stack_alerts.json'
_pending: dict[str, tuple[float, str, str]] = {}
_pending_lock = threading.Lock()
_restart_lock = threading.Lock()
_WARNING = 'Recreating gluetun kills active downloads and takes about 3 minutes to settle; verification can take another 5 minutes.'

# Radarr/Sonarr indexer-health warnings are advisory: Radarr/Sonarr already
# gate them behind their own 6-hour threshold before we ever see them, and
# they're usually self-healing once Prowlarr/the indexer recovers. Everything
# else (unreachable services, DHT zero, Prowlarr health -- which is also how
# a down Byparr/indexer-proxy surfaces, see integrations/nas_stack.py) is
# critical: it's either a hard outage or something that could affect every
# indexer/download, not just one app's view of one indexer.
_ADVISORY_KEYS = frozenset({'radarr-health', 'sonarr-health'})


def _severity(key: str) -> str:
    # A single Prowlarr indexer going down is pushed to Nick (he asked for
    # every one), but it is not an outage of the stack: it must not open the
    # post-recovery grace window that holds back other new conditions.
    if key.startswith('indexer:'):
        return 'warning'
    return 'advisory' if key in _ADVISORY_KEYS else 'critical'


class Notice(NamedTuple):
    key: str
    text: str
    severity: str  # 'critical' | 'warning' | 'advisory'
    kind: str  # 'new' | 'recovered' | 'escalation'


class AlertTracker:
    """Alerts once per condition: fires on new, stays silent while it
    persists, fires one "Recovered" notice when it clears. A condition that
    resolves and later reappears is treated as new again, not a continuation.

    Hysteresis: a condition must be seen on ``ARR_STACK_ALERT_CONFIRM_CHECKS``
    consecutive polls before it is announced, and be absent for
    ``ARR_STACK_RECOVERY_CONFIRM_CHECKS`` consecutive polls before it is
    declared recovered. One-poll blips (a container recreate, a Tailscale
    hiccup on the Optiplex) never reach the phone. While a NAS or Jarvis
    recovery is running (``snapshot['maintenance']``), new conditions keep
    counting but are held back; if they outlast the maintenance they alert
    on the first poll after it ends."""

    def __init__(self, path: Path | None = None):
        self.path = path or _ALERT_FILE
        self.zeros = 0
        self.grace_until = 0.0
        # condition key -> {detail, first_seen, last_escalated, announced, clear_count}
        self.active: dict[str, dict[str, float | str | bool | int]] = {}
        # condition key -> {detail, first_seen, count}: seen, not yet confirmed
        self.pending: dict[str, dict[str, float | str | int]] = {}
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                active = data.get('active')
                if isinstance(active, dict):
                    for key, value in active.items():
                        if isinstance(value, dict) and isinstance(value.get('first_seen'), (int, float)):
                            first_seen = float(value['first_seen'])
                            self.active[str(key)] = {
                                'detail': str(value.get('detail', key)),
                                'first_seen': first_seen,
                                'last_escalated': float(value.get('last_escalated', first_seen)),
                                'announced': bool(value.get('announced', True)),
                                'clear_count': int(value.get('clear_count', 0) or 0),
                            }
                pending = data.get('pending')
                if isinstance(pending, dict):
                    for key, value in pending.items():
                        if isinstance(value, dict) and isinstance(value.get('first_seen'), (int, float)):
                            self.pending[str(key)] = {
                                'detail': str(value.get('detail', key)),
                                'first_seen': float(value['first_seen']),
                                'count': int(value.get('count', 1) or 1),
                            }
                zeros = data.get('zeros')
                # Persisted so a restart right after the zero-DHT threshold does
                # not spuriously "recover" dht-zero while it's still actually zero.
                if isinstance(zeros, int) and zeros >= 0:
                    self.zeros = zeros
                grace_until = data.get('grace_until')
                if isinstance(grace_until, (int, float)):
                    self.grace_until = float(grace_until)
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'active': self.active, 'pending': self.pending, 'zeros': self.zeros, 'grace_until': self.grace_until}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.path)

    def has_unconfirmed(self) -> bool:
        return bool(self.pending) or any(not s['announced'] for s in self.active.values())

    def notices(self, snapshot: dict, now: float | None = None) -> list[Notice]:
        now = time.time() if now is None else now
        nodes = snapshot.get('dht_nodes')
        previous_zeros = self.zeros
        self.zeros = self.zeros + 1 if type(nodes) is int and nodes == 0 else 0
        issues = dict(snapshot['issues'])
        # A recovery legitimately holds DHT at zero for several minutes
        # (gluetun recreate + 130 s Byparr wait + up to 5 min verify), and the
        # NAS watchdog itself only acts after ~15 min, so the threshold sits
        # above a normal recovery window.
        zero_checks = nas_stack.count('ARR_STACK_DHT_ZERO_CHECKS', 10)
        if self.zeros >= zero_checks:
            issues['dht-zero'] = f'qBittorrent DHT has been zero for {self.zeros} consecutive checks'
        confirm_checks = nas_stack.count('ARR_STACK_ALERT_CONFIRM_CHECKS', 3)
        clear_checks = nas_stack.count('ARR_STACK_RECOVERY_CONFIRM_CHECKS', 2)
        maintenance = bool(snapshot.get('maintenance'))

        # One reminder per long window while a condition stays unresolved,
        # instead of either repeating forever or going silent for good.
        escalation_interval = nas_stack.seconds('ARR_STACK_ESCALATION_INTERVAL_S', 21600, 300)
        # New conditions appearing right after a critical recovery are often
        # still settling (recreated containers, restarted apps); suppress
        # their first notice until the grace window ends. Already-active
        # conditions are unaffected -- grace only holds back brand-new ones.
        in_grace = now < self.grace_until
        notices: list[Notice] = []
        changed = self.zeros != previous_zeros

        for key, detail in issues.items():
            state = self.active.get(key)
            if state is None:
                candidate = self.pending.get(key)
                if candidate is None:
                    candidate = self.pending[key] = {'detail': detail, 'first_seen': now, 'count': 0}
                candidate['count'] = int(candidate['count']) + 1
                candidate['detail'] = detail
                changed = True
                # dht-zero already carries its own consecutive-check threshold.
                needed = 1 if key == 'dht-zero' else confirm_checks
                if candidate['count'] < needed or maintenance:
                    continue
                self.pending.pop(key, None)
                self.active[key] = {'detail': detail, 'first_seen': float(candidate['first_seen']), 'last_escalated': now,
                                    'announced': not in_grace, 'clear_count': 0}
                if not in_grace:
                    notices.append(Notice(key, detail, _severity(key), 'new'))
            else:
                if state.get('clear_count'):
                    state['clear_count'] = 0
                    changed = True
                state['detail'] = detail
                if not state['announced']:
                    if not in_grace and not maintenance:
                        state['announced'] = True
                        state['last_escalated'] = now
                        notices.append(Notice(key, detail, _severity(key), 'new'))
                        changed = True
                elif now - state['last_escalated'] >= escalation_interval:
                    state['last_escalated'] = now
                    hours = max(1, int((now - state['first_seen']) // 3600))
                    notices.append(Notice(key, f'Still unresolved after {hours}h: {detail}', _severity(key), 'escalation'))
                    changed = True

        # A blip that cleared before confirmation is dropped silently.
        for key in [k for k in self.pending if k not in issues]:
            self.pending.pop(key)
            changed = True

        recovered_critical = False
        for key in [k for k in self.active if k not in issues]:
            state = self.active[key]
            state['clear_count'] = int(state.get('clear_count', 0)) + 1
            changed = True
            # An unannounced condition has nothing to "recover" publicly, so
            # it can clear on the first clean poll.
            if state['announced'] and state['clear_count'] < clear_checks:
                continue
            self.active.pop(key)
            if state['announced']:
                notices.append(Notice(key, f"Recovered: {state['detail']}", _severity(key), 'recovered'))
                recovered_critical = recovered_critical or _severity(key) == 'critical'

        if recovered_critical:
            grace = nas_stack.seconds('ARR_STACK_POST_RECOVERY_GRACE_S', 300, 0)
            self.grace_until = max(self.grace_until, now + grace)
            changed = True

        if changed:
            self._save()
        return notices


def recovery_in_progress() -> bool:
    """True while a Jarvis-initiated recovery is launching or being followed."""
    return _restart_lock.locked()


_PENDING_TTL_S = 600
_PHRASE = 'confirm restart arr stack'
_TOKEN_RE = re.compile(r'\b[a-f0-9]{8}\b')


def _live_pending(session_id: str, now: float) -> tuple[float, str, str] | None:
    """Caller holds _pending_lock. Drops expired entries as a side effect."""
    for key, value in list(_pending.items()):
        if now - value[0] > _PENDING_TTL_S:
            _pending.pop(key, None)
    return _pending.get(session_id)


def _minutes_left(proposal: tuple[float, str, str], now: float) -> int:
    return max(1, int((_PENDING_TTL_S - (now - proposal[0])) // 60))


def prepare_restart(session_id: str | None) -> str:
    if not session_id:
        return 'Restart refused: a conversation session is required, sir.'
    if not nas_stack.enabled('ARR_STACK_RESTART_ENABLED'):
        return 'Arr stack restart is disabled until the restricted NAS keys are installed and tested, sir.'
    now = time.monotonic()
    with _pending_lock:
        # Re-asking (by Nick or by the model retrying the tool) must not churn
        # the token: reuse the open request until it expires, so a phrase he
        # already has in front of him stays valid.
        existing = _live_pending(session_id, now)
        if existing is not None:
            return f'{_WARNING} A restart request is already open for {_minutes_left(existing, now)} more minute(s). Confirm by replying exactly: {_PHRASE} {existing[1]}'
        request_id = str(uuid.uuid4())
        token = uuid.uuid4().hex[:8]
        _pending[session_id] = (now, token, request_id)
    return f'{_WARNING} Confirm by replying exactly: {_PHRASE} {token}'


def _looks_like_confirmation_attempt(normalized: str, token: str) -> bool:
    """A near miss of the confirm phrase while a request is open.

    Deliberately narrow: a bare "yes" still goes to the model (it may be
    answering something else), but anything carrying the token, starting with
    "confirm", or close to the phrase is clearly aimed at this gate.
    """
    if token in normalized or _TOKEN_RE.fullmatch(normalized):
        return True
    if normalized.startswith(('confirm', 'confrim', 'comfirm')) and any(w in normalized for w in ('restart', 'arr', 'stack', 'rsetart', 'retsart')):
        return True
    words = normalized.split()
    if words and _TOKEN_RE.fullmatch(words[-1]):
        body = ' '.join(words[:-1])
        return difflib.SequenceMatcher(None, body, _PHRASE).ratio() >= 0.75
    return difflib.SequenceMatcher(None, normalized, _PHRASE).ratio() >= 0.8


def _mismatch_reply(sent: str, proposal: tuple[float, str, str], now: float, reason: str) -> str:
    shown = sent.strip().replace('"', "'")[:120]
    return (f'{reason} You sent: "{shown}". I need exactly: {_PHRASE} {proposal[1]} '
            f'— same request, still open for {_minutes_left(proposal, now)} more minute(s), sir. No restart was sent.')


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
    now = time.monotonic()
    if not match:
        with _pending_lock:
            proposal = _live_pending(session_id, now)
        if proposal is not None and _looks_like_confirmation_attempt(normalized, proposal[1]):
            if _TOKEN_RE.fullmatch(normalized):
                reason = 'That was the token without the phrase.'
            else:
                reason = "That doesn't match the confirmation phrase."
            return _mismatch_reply(text, proposal, now, reason)
        return None
    with _pending_lock:
        proposal = _live_pending(session_id, now)
        if not proposal:
            return 'That restart confirmation is invalid or expired, sir. Ask to restart the arr stack again.'
        if match[1] != proposal[1]:
            return _mismatch_reply(text, proposal, now, 'That token does not match the open request.')
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
