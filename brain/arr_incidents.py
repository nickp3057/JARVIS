"""Incident-level push policy for arr-stack alerts.

``brain.arr_stack.AlertTracker`` decides *what* is failing (per-condition
hysteresis, DHT-zero counting). This module decides *whether and how* Nick's
phone hears about it. Found 2026-09-29: a 2-5 minute upstream outage makes
gluetun restart, qBittorrent's DHT goes stale, the NAS DHT watchdog acts
~15 min later, and the recreate it runs produced 6-10 pushes per incident.

Rules, all timed from the Optiplex clock:

- Watchdog hold: once the NAS watchdog (or any shared recovery) is seen
  acting, every arr push is held for ``ARR_STACK_WATCHDOG_HOLD_S``. At the end
  the stack is re-checked: healthy -> one "Recovered after watchdog" summary;
  still failing -> one alert listing what is failing. Only a NAS that stays
  unreachable (all APIs *and* the status SSH down) breaks through the hold.
- Minimum downtime: a condition pushes only once it has been failing for
  ``ARR_STACK_MIN_DOWNTIME_S``. Zero DHT (and qBittorrent "disconnected")
  instead waits ``ARR_STACK_DHT_ALERT_AFTER_S`` so the watchdog gets its turn.
  A condition that never pushed never sends a "Recovered".
- One incident, one notification: every push of an incident shares an APNs
  thread-id and collapse-id, so each update replaces the last and restates
  what is still failing.
- Long indexer backoff: an indexer down for ``ARR_STACK_INDEXER_DIGEST_AFTER_S``
  leaves the incident and goes into one passive daily digest
  (``ARR_STACK_DIGEST_HOUR``) instead of repeating "still unresolved" pushes.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from typing import Any, NamedTuple
import uuid

from brain import arr_stack
from integrations import nas_stack

_STATE_FILE = Path(__file__).resolve().parents[1] / 'memory' / 'arr_stack_incidents.json'
TITLE = 'Jarvis — arr stack'
DIGEST_THREAD = 'arr-stack-digest'
_API_KEYS = ('qbittorrent-api', 'radarr-api', 'sonarr-api', 'prowlarr-api')
# Conditions the NAS DHT watchdog exists to fix; they wait for it to act.
_WATCHDOG_OWNED = frozenset({'dht-zero', 'qbittorrent-connection'})
# Phases of ops/nas/recover_stack.sh, i.e. the full gluetun + dependents recreate.
_FULL_CHAIN_PHASES = frozenset({'starting', 'remove-gluetun', 'recreate-gluetun', 'vpn-settle', 'remove-dependents',
                                'recreate-dependents', 'byparr-cold-start', 'verify', 'complete'})
_SOURCES = {'dht': 'the DHT watchdog', 'indexer': 'the indexer watchdog', 'manual': 'a manual recovery',
            'jarvis': 'a Jarvis-requested recovery'}
# A watchdog stamp and its recovery record are written seconds apart; treat
# anything this close as the same action.
_SAME_ACTION_S = 180


class Push(NamedTuple):
    title: str
    message: str
    thread_id: str
    collapse_id: str
    passive: bool  # delivered quietly: no sound, no banner
    kind: str  # 'alert' | 'urgent' | 'recovered' | 'summary' | 'digest'


class WatchdogAction(NamedTuple):
    at: float  # Optiplex clock
    what: str
    source: str


def _mins(seconds: float) -> int:
    return max(1, int(round(seconds / 60)))


def _digest_hour() -> int:
    try:
        return max(0, min(int(os.getenv('ARR_STACK_DIGEST_HOUR', '9')), 23))
    except ValueError:
        return 9


def watchdog_action(status: tuple[bool, Any] | None, now: float) -> WatchdogAction | None:
    """The NAS's most recent recovery action, from the read-only status key.

    Uses the shared recovery record (``recovery-status.json``) and the
    watchdog cooldown stamps. NAS timestamps are converted by age relative
    to the NAS's own ``observed_at``, so clock skew between hosts cancels."""
    if not status or not status[0] or not isinstance(status[1], dict):
        return None
    data = status[1]
    try:
        observed = float(data['observed_at'])
    except (KeyError, TypeError, ValueError):
        return None
    best: tuple[float, str, str] | None = None
    recovery = data.get('recovery')
    if isinstance(recovery, dict):
        try:
            started = float(recovery.get('started_at') or recovery.get('updated_at') or 0)
        except (TypeError, ValueError):
            started = 0.0
        if started > 0:
            source = str(recovery.get('source') or 'unknown')
            label = _SOURCES.get(source, f'a {source} recovery')
            phase = str(recovery.get('phase') or '')
            what = f'{label} recreated gluetun and the arr containers' if phase in _FULL_CHAIN_PHASES else f'{label} ran a recovery ({phase or "unknown phase"})'
            if recovery.get('state') == 'failed':
                what += f' but it failed at {phase or "an unknown phase"}'
            best = (started, what, source)
    actions = data.get('watchdog_actions')
    for name, label, source in (('.last_action', 'the DHT watchdog restarted qBittorrent', 'dht'),
                                ('.rotate_cooldown', 'the indexer watchdog rotated the VPN exit', 'indexer')):
        try:
            stamp = float((actions or {}).get(name) or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if stamp <= 0 or (best and abs(best[0] - stamp) <= _SAME_ACTION_S):
            continue  # the recovery record already describes this action
        if best is None or stamp > best[0]:
            best = (stamp, label, source)
    if best is None:
        return None
    age = observed - best[0]
    if age < -_SAME_ACTION_S:
        return None  # a stamp from the future: broken NAS clock
    return WatchdogAction(now - max(0.0, age), best[1], best[2])


class IncidentPolicy:
    def __init__(self, path: Path | None = None):
        self.path = path or _STATE_FILE
        self._present: set[str] = set()
        self.state: dict[str, Any] = {'incident': None, 'dht_zero_since': None, 'nas_down_since': None,
                                      'last_acted_at': 0.0, 'digest': {}, 'digest_day': '', 'excused': []}
        # Persisted so a restart mid-hold neither loses the hold nor re-pushes.
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            if isinstance(data.get('incident'), dict) and isinstance(data['incident'].get('pushed'), dict):
                self.state['incident'] = data['incident']
            for key in ('dht_zero_since', 'nas_down_since', 'last_acted_at'):
                if isinstance(data.get(key), (int, float)):
                    self.state[key] = float(data[key])
            if isinstance(data.get('digest'), dict):
                self.state['digest'] = data['digest']
            if isinstance(data.get('digest_day'), str):
                self.state['digest_day'] = data['digest_day']
            if isinstance(data.get('excused'), list):
                self.state['excused'] = [str(k) for k in data['excused']]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.path)

    def _push(self, incident: dict, message: str, *, passive: bool, kind: str) -> Push:
        thread = f"arr-stack-{incident['id']}"
        return Push(TITLE, message, thread, thread, passive, kind)

    def _ready(self, key: str, first_seen: float, now: float) -> bool:
        if key == 'dht-zero':
            since = self.state['dht_zero_since'] or first_seen
            return now - since >= nas_stack.seconds('ARR_STACK_DHT_ALERT_AFTER_S', 1500, 0)
        if key in _WATCHDOG_OWNED:
            return now - first_seen >= nas_stack.seconds('ARR_STACK_DHT_ALERT_AFTER_S', 1500, 0)
        return now - first_seen >= nas_stack.seconds('ARR_STACK_MIN_DOWNTIME_S', 240, 0)

    def evaluate(self, failing: dict[str, tuple[str, float]], snapshot: dict, status: tuple[bool, Any] | None,
                 now: float | None = None) -> list[Push]:
        """Decide this poll's pushes. ``failing`` is ``AlertTracker.failing()``
        without advisory keys; ``status`` is the read-only NAS status, or None
        when it was not fetched this poll."""
        now = time.time() if now is None else now
        s = self.state
        failing = dict(failing)
        pushes: list[Push] = []

        nodes = snapshot.get('dht_nodes')
        s['dht_zero_since'] = (s['dht_zero_since'] or now) if type(nodes) is int and nodes == 0 else None
        issues = snapshot.get('issues') or {}
        # A condition the tracker still lists while it waits out its
        # recovery confirmation must not push for the first time on its way out.
        self._present = set(issues) | ({'dht-zero'} if s['dht_zero_since'] else set())
        nas_silent = all(key in issues for key in _API_KEYS) and (status is None or not status[0])
        s['nas_down_since'] = (s['nas_down_since'] or now) if nas_silent else None

        # Long indexer backoff leaves the incident for the daily digest.
        digest_after = nas_stack.seconds('ARR_STACK_INDEXER_DIGEST_AFTER_S', 21600, 0)
        incident = s['incident']
        for key in [k for k in s['digest'] if k not in failing]:
            s['digest'].pop(key)
        for key, (detail, first_seen) in list(failing.items()):
            if key in s['digest'] or (key.startswith('indexer:') and now - first_seen >= digest_after):
                if key not in s['digest']:
                    match = re.match(r'Indexer (.+?) is down', detail)
                    s['digest'][key] = {'name': match[1] if match else key, 'first_seen': first_seen}
                failing.pop(key)
                if incident:
                    incident['pushed'].pop(key, None)

        # Indexers excused at the end of a watchdog hold stay quiet until they
        # clear (silently) or reach the digest threshold (handled above).
        s['excused'] = [k for k in s['excused'] if k in failing]
        for key in s['excused']:
            failing.pop(key)

        hold_s = nas_stack.seconds('ARR_STACK_WATCHDOG_HOLD_S', 1200, 0)
        action = watchdog_action(status, now)
        if action and hold_s and now - action.at < hold_s:
            hold = incident.get('hold') if incident else None
            if abs(action.at - float(s['last_acted_at'])) > _SAME_ACTION_S:
                s['last_acted_at'] = action.at
                if incident is None:
                    incident = s['incident'] = self._new_incident(now)
                incident['hold'] = {'acted_at': action.at, 'what': action.what, 'source': action.source,
                                    'until': action.at + hold_s, 'healthy_at': None, 'held': []}
            elif hold and abs(action.at - hold['acted_at']) <= _SAME_ACTION_S:
                # The richer recovery record often lands a poll after the stamp.
                hold.update(what=action.what, source=action.source)

        if incident is None and (failing or s['dht_zero_since']):
            incident = s['incident'] = self._new_incident(now)
        if incident is not None:
            hold = incident.get('hold')
            if hold and now < hold['until']:
                pushes.extend(self._during_hold(incident, hold, failing, now))
            elif hold:
                incident['hold'] = None
                # Prowlarr keeps indexers backed off after a recreate until
                # they are re-saved in its UI: expected, not a failed
                # recovery. They go quiet and the daily digest picks them up.
                excused = [k for k in failing if k.startswith('indexer:') and k not in incident['pushed']]
                for key in excused:
                    s['excused'].append(key)
                    failing.pop(key)
                hold['excused'] = len(excused)
                pushes.extend(self._step(incident, failing, now, after_hold=hold))
            else:
                pushes.extend(self._step(incident, failing, now))
            if not failing and not incident['pushed'] and not incident.get('hold') and not s['dht_zero_since']:
                s['incident'] = None

        digest = self._digest(now)
        if digest:
            pushes.append(digest)
        self._save()
        return pushes

    @staticmethod
    def _new_incident(now: float) -> dict:
        return {'id': uuid.uuid4().hex[:8], 'opened_at': now, 'pushed': {}, 'hold': None}

    def _during_hold(self, incident: dict, hold: dict, failing: dict[str, tuple[str, float]], now: float) -> list[Push]:
        for key in failing:
            if key not in hold['held']:
                hold['held'].append(key)
        # Indexer backoff outlives a recreate by design, so it doesn't delay
        # the "took N min" clock.
        stack_down = any(not key.startswith('indexer:') for key in failing)
        hold['healthy_at'] = None if stack_down else (hold['healthy_at'] or now)
        since = self.state['nas_down_since']
        if since and now - since >= nas_stack.seconds('ARR_STACK_NAS_DOWN_ALERT_S', 600, 0) and 'nas-unreachable' not in incident['pushed']:
            detail = (f'NAS unreachable for {_mins(now - since)} min: qBittorrent, Radarr, Sonarr and Prowlarr '
                      'and the NAS status SSH are all down')
            incident['pushed']['nas-unreachable'] = {'detail': detail, 'last_push': now}
            return [self._push(incident, f'{detail}. This outlasts a normal watchdog recovery.', passive=False, kind='urgent')]
        return []

    def _step(self, incident: dict, failing: dict[str, tuple[str, float]], now: float, after_hold: dict | None = None) -> list[Push]:
        pushed: dict[str, dict] = incident['pushed']
        if after_hold and not failing:
            took = _mins((after_hold['healthy_at'] or now) - after_hold['acted_at'])
            pushed.clear()
            if after_hold['source'] == 'jarvis':
                return []  # the confirmed-restart follow-up already reported
            message = f"Recovered after watchdog: {after_hold['what']}, took {took} min."
            if after_hold['held']:
                message += f" Held {len(after_hold['held'])} alert(s) while it settled."
            if after_hold.get('excused'):
                message += (f" {after_hold['excused']} Prowlarr indexer(s) still backed off, as usual after a recreate; "
                            'they may need re-saving in the Prowlarr UI and will be in the daily digest if it lasts.')
            return [self._push(incident, message, passive=True, kind='summary')]

        # Anything still failing when the hold ends has outlasted recovery.
        new = [k for k in failing if k not in pushed and k in self._present
               and (after_hold or self._ready(k, failing[k][1], now))]
        interval = nas_stack.seconds('ARR_STACK_ESCALATION_INTERVAL_S', 21600, 300)
        escalated = [k for k in failing if k in pushed and not k.startswith('indexer:')
                     and now - float(pushed[k]['last_push']) >= interval]
        recovered = [k for k in pushed if k not in failing]
        if not (new or escalated or recovered):
            return []
        parts: list[str] = []
        if new:
            lead = (f"Arr stack still failing {_mins(now - after_hold['acted_at'])} min after {after_hold['what']}: "
                    if after_hold else '')
            parts.append(lead + '; '.join(failing[k][0] for k in new))
        for key in escalated:
            parts.append(f'Still failing after {max(1, int((now - failing[key][1]) // 3600))}h: {failing[key][0]}')
        if recovered:
            parts.append('Recovered: ' + '; '.join(str(pushed[k]['detail']) for k in recovered))
        # The collapse-id replaces the previous notification, so restate
        # whatever it was reporting that is still true.
        ongoing = [failing[k][0] for k in pushed if k in failing and k not in escalated]
        if ongoing:
            parts.append('Still failing: ' + '; '.join(ongoing))
        for key in recovered:
            pushed.pop(key)
        for key in new + escalated:
            pushed[key] = {'detail': failing[key][0], 'last_push': now}
        loud = bool(new or escalated)
        return [self._push(incident, '. '.join(parts) + '.', passive=not loud, kind='alert' if loud else 'recovered')]

    def _digest(self, now: float) -> Push | None:
        s = self.state
        local = datetime.fromtimestamp(now)
        today = local.strftime('%Y-%m-%d')
        if not s['digest'] or s['digest_day'] == today or local.hour < _digest_hour():
            return None
        s['digest_day'] = today
        entries = sorted(s['digest'].values(), key=lambda e: e['first_seen'])
        names = ', '.join(f"{e['name']} (down {max(1, int((now - e['first_seen']) // 3600))}h)" for e in entries)
        message = (f'Daily indexer digest: {len(entries)} Prowlarr indexer(s) still backed off: {names}. '
                   'Prowlarr can keep an indexer in backoff after the cause is gone; it may need re-saving '
                   'in the Prowlarr UI (open the indexer, Save).')
        return Push(TITLE, message, DIGEST_THREAD, DIGEST_THREAD, True, 'digest')


def run_cycle(tracker: arr_stack.AlertTracker, policy: IncidentPolicy, snapshot: dict,
              status: tuple[bool, Any] | None, now: float | None = None) -> tuple[list[Push], list[str]]:
    """One monitor poll: (phone pushes, advisory inbox lines)."""
    now = time.time() if now is None else now
    notices = tracker.notices(snapshot, now)
    advisory = [n.text for n in notices if n.severity == 'advisory']
    failing = {k: v for k, v in tracker.failing().items() if k not in arr_stack._ADVISORY_KEYS}
    return policy.evaluate(failing, snapshot, status, now), advisory
