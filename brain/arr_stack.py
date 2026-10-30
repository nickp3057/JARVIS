"""Runtime arr monitoring state and session-bound, human-only recovery approval."""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Callable, NamedTuple
import uuid

import requests

from integrations import nas_stack, qbittorrent, servarr_common

_ALERT_FILE = Path(__file__).resolve().parents[1] / 'memory' / 'arr_stack_alerts.json'
_pending: dict[str, tuple[float, str, str]] = {}
_pending_lock = threading.Lock()
_restart_lock = threading.Lock()

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

    def failing(self) -> dict[str, tuple[str, float]]:
        """Conditions past the confirmation hysteresis: key -> (detail, first_seen).

        Includes ones the maintenance hold kept pending and ones still
        waiting out ``ARR_STACK_RECOVERY_CONFIRM_CHECKS``. This is what
        ``brain.arr_incidents`` decides pushes from; ``notices()`` remains
        the per-condition transition log."""
        confirm_checks = nas_stack.count('ARR_STACK_ALERT_CONFIRM_CHECKS', 3)
        out = {key: (str(state['detail']), float(state['first_seen'])) for key, state in self.active.items()}
        for key, candidate in self.pending.items():
            if int(candidate['count']) >= (1 if key == 'dht-zero' else confirm_checks):
                out[key] = (str(candidate['detail']), float(candidate['first_seen']))
        return out

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


_update_hold_until = 0.0


def recovery_in_progress() -> bool:
    """True while a Jarvis-initiated recovery or update is running."""
    return _restart_lock.locked() or time.time() < _update_hold_until


def prepare_update(session_id: str | None) -> str:
    """Stage "update the arr stack" for Nick's yes (containers restart, so
    downloads pause for a few minutes)."""
    from brain import confirm_gate

    checked, downloading = qbittorrent.active_downloads()
    if not checked:
        why = f"I couldn't check qBittorrent ({downloading}), so downloads may be interrupted"
    elif downloading:
        why = f"{len(downloading)} torrent(s) downloading will pause while containers restart"
    else:
        why = "nothing is downloading"
    return confirm_gate.propose(
        session_id,
        f"update the arr stack on the NAS (pull new images, restart only what changed, about 5 minutes; {why})",
        start_update,
    )


def start_update() -> str:
    """Kick off the NAS updater now; holds alerts while containers restart."""
    global _update_hold_until
    ok, result = nas_stack.start_update(str(uuid.uuid4()))
    if not ok:
        return f"The NAS refused the update, sir: {result}"
    _update_hold_until = time.time() + nas_stack.seconds('ARR_STACK_UPDATE_HOLD_S', 900, 60)
    return ("Update started on the NAS, sir. It pulls new images and restarts only the containers "
            "that changed, about five minutes. Alerts are held until it settles.")


_PENDING_TTL_S = 600
_LONG_PHRASE = 'confirm restart arr stack'
_CODE_RE = re.compile(r'(?:confirm(?: restart(?: the)? arr stack)? )?(\d{4})')
_RESTART_RE = re.compile(r'(?:(?:hey )?jarvis,? )?(?:(?:please|can you|could you) )?restart (?:the )?arr stack(?: please| now| for me)?')
_YES = frozenset({'yes', 'y', 'yeah', 'yep', 'yup', 'yes please', 'yes sir', 'yes do it', 'do it',
                  'go', 'go ahead', 'proceed', 'ok', 'okay', 'confirm', 'confirmed'})
# Sessions whose very next message may approve with a bare "yes". Every
# message disarms it, so a "yes" sent later can't approve a stale request.
_armed: set[str] = set()


def _live_pending(session_id: str, now: float) -> tuple[float, str, str] | None:
    """Caller holds _pending_lock. Drops expired entries as a side effect."""
    for key, value in list(_pending.items()):
        if now - value[0] > _PENDING_TTL_S:
            _pending.pop(key, None)
    return _pending.get(session_id)


def _minutes_left(proposal: tuple[float, str, str], now: float) -> int:
    return max(1, int((_PENDING_TTL_S - (now - proposal[0])) // 60))


def _launch(request_id: str) -> tuple[bool, str]:
    if not _restart_lock.acquire(blocking=False):
        return False, 'An arr recovery request is already being monitored, sir. No second restart was requested.'
    try:
        # On success the follow-up thread takes ownership of the lock and
        # releases it when the outcome is known; it doubles as the monitor's
        # "Jarvis recovery in progress" maintenance signal.
        return True, _run_recovery(request_id)
    except BaseException:
        _restart_lock.release()
        raise


def prepare_restart(session_id: str | None, *, direct: bool = False) -> str:
    """Restart at once when Nick asked directly and nothing is downloading;
    otherwise open (or reuse) a session-bound request with a 4-digit code.

    The model's tool path never restarts by itself (direct=False): it only
    opens the request, so a restart always follows a message Nick sent."""
    if not session_id:
        return 'Restart refused: a conversation session is required, sir.'
    if not nas_stack.enabled('ARR_STACK_RESTART_ENABLED'):
        return 'Arr stack restart is disabled until the restricted NAS keys are installed and tested, sir.'
    checked, downloading = qbittorrent.active_downloads()
    if direct and checked and not downloading:
        with _pending_lock:
            _pending.pop(session_id, None)
            _armed.discard(session_id)
        started, reply = _launch(str(uuid.uuid4()))
        return f'Nothing downloading, restarting now. {reply}' if started else reply
    now = time.monotonic()
    with _pending_lock:
        # Re-asking (by Nick or by the model retrying the tool) must not churn
        # the code: reuse the open request until it expires, so a code he
        # already has in front of him stays valid.
        proposal = _live_pending(session_id, now)
        reused = proposal is not None
        if proposal is None:
            proposal = (now, f'{secrets.randbelow(10000):04d}', str(uuid.uuid4()))
            _pending[session_id] = proposal
        _armed.add(session_id)
    if not checked:
        why = f"I couldn't check qBittorrent ({downloading}), so active downloads may be cut off."
    elif downloading:
        names = ', '.join(downloading[:3]) + (f' and {len(downloading) - 3} more' if len(downloading) > 3 else '')
        why = f'{len(downloading)} torrent(s) actively downloading ({names}); restarting kills them.'
    else:
        why = 'Nothing is downloading right now.'
    same = ' Same request as before.' if reused else ''
    return (f'{why} Recovery takes about 3 minutes plus up to 5 to verify.{same} '
            f'Reply "yes" now to restart, or "confirm {proposal[1]}" within {_minutes_left(proposal, now)} minute(s), sir.')


def _looks_like_confirmation_attempt(normalized: str) -> bool:
    """A near miss of the confirmation while a request is open.

    Deliberately narrow: a late bare "yes" still goes to the model (it may be
    answering something else), but a bare code, anything starting with
    "confirm" about the restart, or text close to the long phrase is clearly
    aimed at this gate."""
    if re.fullmatch(r'\d{3,5}|[a-f0-9]{8}', normalized):
        return True
    if normalized.startswith(('confirm', 'confrim', 'comfirm')) and (
            normalized in _YES or re.search(r'\d|restart|arr|stack|rsetart|retsart', normalized)):
        return True
    words = normalized.split()
    if words and re.fullmatch(r'\d+|[a-f0-9]{8}', words[-1]):
        body = ' '.join(words[:-1])
        return difflib.SequenceMatcher(None, body, _LONG_PHRASE).ratio() >= 0.75
    return difflib.SequenceMatcher(None, normalized, _LONG_PHRASE).ratio() >= 0.8


def _mismatch_reply(sent: str, proposal: tuple[float, str, str], now: float, reason: str) -> str:
    shown = sent.strip().replace('"', "'")[:120]
    return (f'{reason} You sent: "{shown}". Reply "confirm {proposal[1]}" '
            f'— same request, still open for {_minutes_left(proposal, now)} more minute(s), sir. No restart was sent.')


def conversation_action(text: str, session_id: str) -> str | None:
    """Runs only on original user text at the agent boundary, never tool args."""
    normalized = ' '.join(text.lower().strip().split()).rstrip('.!?')
    with _pending_lock:
        armed = session_id in _armed
        _armed.discard(session_id)
    if _RESTART_RE.fullmatch(normalized):
        return prepare_restart(session_id, direct=True)
    if normalized in {'cancel arr restart', 'cancel restart arr stack'} or (
            armed and normalized in {'cancel', 'no', 'nope', 'nevermind', 'never mind', 'not now'}):
        with _pending_lock:
            _pending.pop(session_id, None)
        return 'Arr stack restart cancelled, sir.'
    match = _CODE_RE.fullmatch(normalized)
    bare_yes = match is None and armed and normalized in _YES
    now = time.monotonic()
    if match is None and not bare_yes:
        with _pending_lock:
            proposal = _live_pending(session_id, now)
        if proposal is not None and _looks_like_confirmation_attempt(normalized):
            if normalized in _YES:
                reason = f'Another message came in after my prompt, so "{normalized}" alone no longer counts.'
            else:
                reason = "That doesn't match the confirmation."
            return _mismatch_reply(text, proposal, now, reason)
        return None
    with _pending_lock:
        proposal = _live_pending(session_id, now)
        if not proposal:
            if normalized.isdigit():
                return None  # just a number; nothing is waiting for it
            return 'That restart confirmation is invalid or expired, sir. Ask to restart the arr stack again.'
        if match is not None and match[1] != proposal[1]:
            return _mismatch_reply(text, proposal, now, 'That code does not match the open request.')
        # Consume before any network side effect, including ambiguous failures.
        _pending.pop(session_id, None)
    if not nas_stack.enabled('ARR_STACK_RESTART_ENABLED'):
        return 'Arr stack restart is now disabled; no restart was requested, sir.'
    return _launch(proposal[2])[1]


# Recovery chain timing on the NAS (ops/nas/recover_stack.sh): ~25 s to
# recreate gluetun and its dependents, then 20 s VPN settle + 130 s Byparr
# cold start, then up to 300 s of verification. Live probes cannot declare
# success before the chain has had time to actually recreate everything.
_MIN_CHAIN_S = 160
_POLL_S = 15
_notifier: Callable[[str, str], None] | None = None


def set_recovery_notifier(callback: Callable[[str, str], None] | None) -> None:
    """Register how the follow-up reaches Nick: callback(title, message)."""
    global _notifier
    _notifier = callback


def _probe_http(service: str) -> bool:
    if service in {'radarr', 'sonarr'}:
        if not servarr_common.is_configured(service):
            return False
        ok, _ = servarr_common.request(service, 'GET', '/api/v3/system/status')
        return ok
    base = os.getenv('PROWLARR_URL', '').strip().rstrip('/')
    if not base:
        return False
    try:
        response = requests.get(base + '/ping', timeout=8, allow_redirects=False)
        return response.status_code == 200
    except requests.RequestException:
        return False


def service_states(nas_recovery: dict | None = None) -> dict[str, tuple[bool, str]]:
    """Live reachability of each stack component: name -> (up, detail).

    Plain HTTP from the Optiplex; no SSH beyond the optional read-only status
    the caller already fetched. qBittorrent shares gluetun's network
    namespace, so qBit answering with positive DHT is proof the tunnel is up
    even without the status key."""
    states: dict[str, tuple[bool, str]] = {}
    ok, info = nas_stack.network_status()
    nodes = info.get('dht_nodes') if ok else None
    states['qBittorrent'] = (ok, f'DHT {nodes}' if ok and nodes is not None else ('reachable' if ok else 'unreachable'))
    health = (nas_recovery or {}).get('gluetun_health')
    if health in {'healthy', 'starting', 'unhealthy'}:
        states['gluetun'] = (health == 'healthy', health)
    elif ok and type(nodes) is int and nodes > 0:
        states['gluetun'] = (True, 'tunnel passing traffic')
    else:
        states['gluetun'] = (False, 'no traffic through the tunnel yet' if ok else 'unknown (qBit unreachable)')
    for name, service in (('Radarr', 'radarr'), ('Sonarr', 'sonarr'), ('Prowlarr', 'prowlarr')):
        up = _probe_http(service)
        states[name] = (up, 'up' if up else 'unreachable')
    return states


def _summarize(states: dict[str, tuple[bool, str]], still_settling: bool) -> str:
    down_word = 'still starting' if still_settling else 'failed'
    up = [f'{name} up' + (f' ({detail})' if detail not in {'up', 'reachable'} else '') for name, (ok, detail) in states.items() if ok]
    down = [f'{name} {down_word} ({detail})' for name, (ok, detail) in states.items() if not ok]
    return '; '.join(down + up) + '.'


def _follow_recovery(request_id: str, accepted: bool) -> str:
    """Poll until the recovery outcome is known and return the final report.

    Never retries the launch. Uses the NAS's correlated status when the
    read-only key works, and live service probes either way, so a missing
    status key yields a real per-service report instead of "unknown"."""
    short = request_id[:8]
    started_at = time.monotonic()
    follow_s = nas_stack.seconds('ARR_RECOVERY_FOLLOW_S', 720, 300)
    deadline = started_at + follow_s
    status_known = False
    last_phase = None
    while True:
        now = time.monotonic()
        ok, payload = nas_stack.watchdog_status()
        recovery = payload.get('recovery') if ok and isinstance(payload, dict) else None
        if isinstance(recovery, dict) and recovery.get('request_id') == request_id:
            status_known = True
            last_phase = recovery.get('phase', last_phase)
            state = recovery.get('state')
            if state == 'running' and not accepted:
                # Even if the launch reply was lost, a correlated running state
                # proves the job started.
                accepted = True
            if state == 'failed':
                states = service_states(recovery)
                return (f"Arr recovery failed at {recovery.get('phase', 'an unknown phase')} (exit {recovery.get('exit_code', '?')}), sir. "
                        f'Current state: {_summarize(states, still_settling=False)} Request {short}.')
            if state == 'success':
                states = service_states(recovery)
                nodes = recovery.get('dht_nodes')
                if all(up for up, _ in states.values()):
                    return f'Arr stack recovered, sir. NAS verified gluetun healthy with DHT {nodes}; from the Optiplex: {_summarize(states, still_settling=False)}'
                return (f'The NAS verified the recovery (gluetun healthy, DHT {nodes}), sir, but from the Optiplex: '
                        f'{_summarize(states, still_settling=now < deadline)} Request {short}.')
        elif not status_known and now - started_at >= _MIN_CHAIN_S:
            # No status key (or no correlated record): judge from live probes
            # once the chain has had time to recreate everything.
            states = service_states()
            if accepted and all(up for up, _ in states.values()):
                return f'Arr stack is back up, sir: {_summarize(states, still_settling=False)}'
        if not accepted and not status_known and now - started_at >= 60:
            states = service_states()
            return ('The NAS never confirmed the restart request, sir, and no matching recovery started, so nothing was retried. '
                    f'Current state: {_summarize(states, still_settling=False)}')
        if now >= deadline:
            break
        time.sleep(_POLL_S)
    states = service_states()
    minutes = int(follow_s // 60)
    phase = f' (last NAS phase: {last_phase})' if last_phase else ''
    if all(up for up, _ in states.values()):
        return f'The NAS has not posted a final result after {minutes} minutes{phase}, sir, but every service is up: {_summarize(states, still_settling=False)}'
    return f'Arr recovery has not settled after {minutes} minutes{phase}, sir: {_summarize(states, still_settling=False)} No automatic retry was sent. Request {short}.'


def _follow_and_report(request_id: str, accepted: bool) -> None:
    try:
        try:
            message = _follow_recovery(request_id, accepted)
        except Exception as exc:  # noqa: BLE001 -- always tell Nick something
            message = f'I lost track of the arr recovery ({type(exc).__name__}), sir. No retry was sent; ask me for arr stack status.'
        callback = _notifier
        if callback is not None:
            try:
                callback('Jarvis — arr recovery', message)
            except Exception:  # noqa: BLE001
                pass
    finally:
        _restart_lock.release()


def _run_recovery(request_id: str) -> str:
    """Launch once, hand the follow-up to a background thread, reply now.

    Caller holds _restart_lock; the follow-up thread releases it. Waiting the
    full ~8 minute chain inside the turn blocked every other conversation and
    overran the companion app's 3-minute request timeout."""
    # Never retry the launch: a timeout might mean the detached job has started.
    ok, response = nas_stack.start_recovery(request_id)
    accepted = ok and isinstance(response, dict) and response.get('request_id') == request_id and response.get('state') in {'accepted', 'already_started'}
    threading.Thread(target=_follow_and_report, args=(request_id, accepted), daemon=True, name='arr-recovery-follow').start()
    if accepted:
        return ('Restart accepted by the NAS, sir. Gluetun recreation takes about 3 minutes and verification up to 5 more; '
                "I'll report gluetun, qBittorrent, Radarr, Sonarr and Prowlarr status when it settles.")
    reason = response if isinstance(response, str) else 'unexpected reply'
    return (f"The NAS didn't confirm the restart request ({reason}), sir. It may still have started, so I won't retry; "
            "I'll watch for a minute and report what I find.")


def describe_status() -> str:
    snapshot = nas_stack.poll_health()
    issues = '; '.join(snapshot['issues'].values()) or 'All configured APIs are reachable with no health errors'
    return f"{issues}. DHT: {snapshot['dht_nodes']}; connection: {snapshot['connection_status']}. {nas_stack.watchdog_note()}"
