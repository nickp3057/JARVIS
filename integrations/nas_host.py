"""NAS host health: containers, RAID, SMART, volumes, temperatures, updater.

The NAS's root cron writes ``host-health.json`` (``ops/nas/host_health.py``)
and the read-only status key returns it as ``host_health``. This module reads
that payload; it never talks to the NAS itself and never acts.

``HostWatch`` turns snapshots into debounced alerts for the runtime monitor.
The arr containers in gluetun's network are skipped by default: the arr
monitor and ``brain/arr_incidents.py`` already own them.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
from pathlib import Path
import time
from typing import Any, NamedTuple

from integrations import nas_stack

_STATE_FILE = Path(__file__).resolve().parents[1] / 'memory' / 'nas_host_state.json'
_DEFAULT_IGNORE = 'gluetun,qbittorrent,prowlarr,radarr,sonarr,flaresolverr'
_STALE_S = 20 * 60
# Hardware and one-off events alert on first sight; the rest need
# NAS_HOST_CONFIRM_CHECKS consecutive polls, so a container mid-restart or a
# brief temperature spike stays quiet.
_IMMEDIATE = ('raid:', 'smart:', 'sectors:', 'disk-errors', 'restarts:')
# Events: nothing to "recover" from; re-alert only when the detail changes.
_EVENTS = ('restarts:', 'disk-errors')
_REALERT_ON_CHANGE = ('sectors:', 'disk-errors')
_RESTART_POLICIES = {'always', 'unless-stopped', 'on-failure'}


class HostAlert(NamedTuple):
    title: str
    message: str
    severity: str  # warning | critical | info
    push: bool
    condition_keys: tuple[str, ...] = ()


def monitor_enabled() -> bool:
    return os.getenv('NAS_HOST_MONITOR_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'on'} \
        and nas_stack.status_configured()


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def ignored_containers() -> set[str]:
    return {x.strip() for x in os.getenv('NAS_HOST_IGNORE_CONTAINERS', _DEFAULT_IGNORE).split(',') if x.strip()}


def from_status(status: tuple[bool, Any] | None) -> tuple[dict, float] | None:
    """(host health, NAS observed_at) from a status-key result, or None."""
    if not status or not status[0] or not isinstance(status[1], dict):
        return None
    health = status[1].get('host_health')
    if not isinstance(health, dict):
        return None
    try:
        observed = float(status[1].get('observed_at') or health.get('generated_at') or time.time())
    except (TypeError, ValueError):
        observed = time.time()
    return health, observed


def _list(health: dict, key: str) -> list[dict]:
    value = health.get(key)
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _disk_label(disk: dict) -> str:
    return f"{disk.get('device')}{' (' + str(disk['model']) + ')' if disk.get('model') else ''}"


def _sectors(disk: dict) -> tuple[int, str]:
    parts = [(disk.get('reallocated'), 'reallocated'), (disk.get('pending'), 'pending'),
             (disk.get('offline_uncorrectable'), 'uncorrectable'), (disk.get('media_errors'), 'media errors')]
    bad = [(int(n), label) for n, label in parts if isinstance(n, int) and n > 0]
    return sum(n for n, _ in bad), ', '.join(f'{n} {label}' for n, label in bad)


def conditions(health: dict, observed: float, restarts_last_hour: dict[str, int] | None = None) -> dict[str, tuple[str, str]]:
    """Everything wrong in one snapshot: ``{key: (severity, detail)}``."""
    out: dict[str, tuple[str, str]] = {}
    try:
        age = observed - float(health.get('generated_at') or 0)
    except (TypeError, ValueError):
        age = _STALE_S + 1
    if age > _STALE_S:
        out['host-health-stale'] = ('warning', f'NAS host-health snapshot is {int(age // 60)} min old: '
                                               'the root cron job (host_health.py) is not running')
    for array in _list(health, 'raid'):
        if array.get('degraded'):
            sync = f", {array['sync']}" if array.get('sync') else ''
            out[f"raid:{array.get('name')}"] = ('critical', f"RAID {array.get('name')} is degraded "
                                                f"[{array.get('slots')}] [{array.get('pattern')}]{sync}")
    disk_temp = _number('NAS_DISK_TEMP_ALERT_C', 55)
    for disk in _list(health, 'disks'):
        label = _disk_label(disk)
        if disk.get('smart_passed') is False:
            out[f"smart:{disk.get('device')}"] = ('critical', f'Disk {label} FAILED its SMART health check')
        total, text = _sectors(disk)
        if total:
            out[f"sectors:{disk.get('device')}"] = ('warning', f'Disk {label} reports bad sectors: {text}')
        temp = disk.get('temp_c')
        if isinstance(temp, (int, float)) and temp >= disk_temp:
            out[f"disk-temp:{disk.get('device')}"] = ('warning', f'Disk {label} is at {temp:.0f} °C')
    cpu = health.get('cpu_temp_c')
    if isinstance(cpu, (int, float)) and cpu >= _number('NAS_CPU_TEMP_ALERT_C', 85):
        out['cpu-temp'] = ('warning', f'NAS CPU is at {cpu:.0f} °C')
    volume_pct = _number('NAS_VOLUME_ALERT_PCT', 90)
    for volume in _list(health, 'volumes'):
        pct = volume.get('used_pct')
        if isinstance(pct, (int, float)) and pct >= volume_pct:
            free_gb = int(volume.get('free_bytes') or 0) / 1e9
            out[f"volume:{volume.get('path')}"] = ('critical' if pct >= 97 else 'warning',
                                                   f"{volume.get('path')} is {pct:.0f}% full ({free_gb:.0f} GB free)")
    ignored = ignored_containers()
    for c in _list(health, 'containers'):
        name = str(c.get('name', ''))
        if not name or name in ignored:
            continue
        if c.get('state') == 'restarting':
            out[f'container:{name}'] = ('warning', f'Container {name} is stuck restarting')
        elif c.get('state') != 'running' and c.get('restart_policy') in _RESTART_POLICIES:
            oom = ', killed for running out of memory' if c.get('oom_killed') else ''
            out[f'container:{name}'] = ('warning', f"Container {name} is {c.get('state')} "
                                                   f"(exit code {c.get('exit_code')}{oom})")
        elif c.get('health') == 'unhealthy':
            out[f'container:{name}'] = ('warning', f'Container {name} reports unhealthy')
    limit = int(_number('NAS_CONTAINER_RESTARTS_ALERT', 3))
    for name, count in (restarts_last_hour or {}).items():
        if count >= limit and name not in ignored:
            out[f'restarts:{name}'] = ('warning', f'Container {name} restarted {count} times in the last hour')
    return out


def updater_failure(health: dict) -> tuple[str, str] | None:
    """(last run, problem) when the image updater's last run failed or left
    images behind (``FAILED:`` / ``PARTIAL:`` in its status file), else None."""
    updater = health.get('updater') if isinstance(health, dict) else None
    if not isinstance(updater, dict):
        return None
    text = str(updater.get('text') or '')
    bad = re.search(r'^(?:FAILED|PARTIAL):\s*(.+)$', text, re.M)
    if not bad:
        return None
    return str(updater.get('last_run') or updater.get('mtime') or ''), bad[1].strip()[:200]


class HostWatch:
    """Debounced alerts from successive snapshots; state survives restarts."""

    def __init__(self, path: Path | None = None):
        self.path = path or _STATE_FILE
        self.state: dict[str, Any] = {'alerted': {}, 'pending': {}, 'clear': {}, 'restarts': {}, 'kern_last': None}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            for key in self.state:
                if isinstance(data.get(key), type(self.state[key])) or (key == 'kern_last' and isinstance(data.get(key), str)):
                    self.state[key] = data[key]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.path)

    def _restart_counts(self, health: dict, now: float) -> dict[str, int]:
        """Restarts per container over the last hour, from RestartCount samples."""
        history: dict[str, list] = self.state['restarts']
        out: dict[str, int] = {}
        for c in _list(health, 'containers'):
            name, count = str(c.get('name', '')), c.get('restart_count')
            if not name or not isinstance(count, int):
                continue
            samples = [s for s in history.get(name, []) if now - s[0] <= 3600] + [[now, count]]
            history[name] = samples
            # A recreated container restarts its count at 0; never go negative.
            out[name] = max(0, count - min(s[1] for s in samples))
        for name in [n for n in history if n not in out]:
            history.pop(name)
        return out

    def _kernel_event(self, health: dict) -> tuple[str, str] | None:
        errors = health.get('kernel_disk_errors')
        if not isinstance(errors, dict):
            return None
        recent = [str(x) for x in errors.get('recent') or []]
        last = recent[-1] if recent else ''
        previous = self.state['kern_last']
        self.state['kern_last'] = last
        if previous is None or not last or last == previous:
            return None  # first sight is the baseline; old lines are not news
        new = recent[recent.index(previous) + 1:] if previous in recent else recent
        return 'warning', f'Kernel disk errors on the NAS ({len(new)} new): {new[-1][:220]}'

    def step(self, status: tuple[bool, Any] | None, now: float | None = None) -> list[HostAlert]:
        """One poll. No status (NAS down, key broken) is not judged here."""
        now = time.time() if now is None else now
        parsed = from_status(status)
        if parsed is None:
            return []
        health, observed = parsed
        current = conditions(health, observed, self._restart_counts(health, now))
        kernel = self._kernel_event(health)
        if kernel:
            current['disk-errors'] = kernel
        confirm = max(1, int(_number('NAS_HOST_CONFIRM_CHECKS', 2)))
        s = self.state
        new: list[tuple[str, str, str]] = []
        for key, (severity, detail) in current.items():
            s['clear'].pop(key, None)
            if key in s['alerted']:
                if key.startswith(_REALERT_ON_CHANGE) and s['alerted'][key] != detail:
                    s['alerted'][key] = detail
                    new.append((key, severity, detail))
                continue
            s['pending'][key] = s['pending'].get(key, 0) + 1
            if key.startswith(_IMMEDIATE) or s['pending'][key] >= confirm:
                s['pending'].pop(key)
                s['alerted'][key] = detail
                new.append((key, severity, detail))
        for key in [k for k in s['pending'] if k not in current]:
            s['pending'].pop(key)
        recovered: list[str] = []
        for key in [k for k in s['alerted'] if k not in current]:
            if key.startswith(_EVENTS):
                s['alerted'].pop(key)
                continue
            s['clear'][key] = s['clear'].get(key, 0) + 1
            if s['clear'][key] >= 2:
                recovered.append(s['alerted'].pop(key))
                s['clear'].pop(key)
        self._save()
        alerts: list[HostAlert] = []
        if new:
            severity = 'critical' if any(sev == 'critical' for _, sev, _ in new) else 'warning'
            title = f'NAS: {new[0][2]}' if len(new) == 1 else f'NAS: {len(new)} problems'
            alerts.append(HostAlert(title[:120], '; '.join(d for _, _, d in new) + '.', severity, True,
                                    tuple(k for k, _, _ in new)))
        if recovered:
            alerts.append(HostAlert('NAS: recovered', 'Cleared: ' + '; '.join(recovered) + '.', 'info', False))
        return alerts


# --- incident-report helpers --------------------------------------------------

def _when(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime('%a %H:%M')


def describe(health: dict, observed: float) -> list[str]:
    """Plain lines for an incident report."""
    lines: list[str] = []
    try:
        lines.append(f"Snapshot taken {max(0, int((observed - float(health.get('generated_at') or 0)) // 60))} min before this check.")
    except (TypeError, ValueError):
        pass
    containers = _list(health, 'containers')
    bad = [c for c in containers if c.get('state') != 'running' or c.get('health') == 'unhealthy']
    lines += [f"Container {c.get('name')}: {c.get('state')}{', ' + str(c['health']) if c.get('health') else ''}, "
              f"{c.get('restart_count')} restarts" for c in bad[:8]]
    recent = sorted((c for c in containers if isinstance(c.get('started_at'), (int, float))
                     and observed - c['started_at'] <= 6 * 3600), key=lambda c: c['started_at'])
    if recent:
        lines.append('Started in the last 6 h: ' + ', '.join(f"{c.get('name')} ({_when(c['started_at'])})" for c in recent[:10]))
    if containers and not bad:
        lines.append(f'All {len(containers)} containers running.')
    updater = health.get('updater')
    if isinstance(updater, dict):
        applied = ', '.join(updater.get('applied') or []) or 'nothing'
        lines.append(f"Image updater last ran {updater.get('last_run') or '?'}; applied: {applied}.")
        failure = updater_failure(health)
        if failure:
            lines.append(f"Its last run had a problem: {failure[1]}.")
    for array in _list(health, 'raid'):
        single = ' (single disk, no redundancy)' if array.get('slots') == '1/1' else ''
        lines.append(f"RAID {array.get('name')} {array.get('level')} [{array.get('slots')}] [{array.get('pattern')}]"
                     f"{' DEGRADED' if array.get('degraded') else ''}{single}")
    for disk in _list(health, 'disks'):
        if disk.get('error'):
            lines.append(f"Disk {_disk_label(disk)}: SMART unavailable ({disk['error']})")
            continue
        verdict = {True: 'SMART passed', False: 'SMART FAILED', None: 'SMART unknown'}[disk.get('smart_passed')]
        _, sectors = _sectors(disk)
        extra = [f"{disk['temp_c']} °C" if disk.get('temp_c') is not None else '',
                 f"{disk['power_on_hours']} h powered on" if disk.get('power_on_hours') else '',
                 sectors, 'asleep' if disk.get('standby') else '']
        lines.append(f"Disk {_disk_label(disk)}: {verdict}" + ''.join(f', {x}' for x in extra if x))
    for volume in _list(health, 'volumes'):
        lines.append(f"{volume.get('path')}: {volume.get('used_pct')}% used, {int(volume.get('free_bytes') or 0) / 1e9:.0f} GB free")
    if health.get('cpu_temp_c') is not None:
        lines.append(f"CPU {health['cpu_temp_c']} °C")
    errors = health.get('kernel_disk_errors')
    if isinstance(errors, dict) and errors.get('count'):
        lines.append(f"Kernel disk errors in the recent log: {errors['count']}; last: {str((errors.get('recent') or [''])[-1])[:200]}")
    if health.get('errors'):
        lines.append('Snapshot gaps: ' + '; '.join(str(e) for e in health['errors'][:4]))
    return lines


def causes(health: dict, observed: float, alert_at: float) -> list[str]:
    """Host-level suspects for an incident, most serious first."""
    out = [detail for key, (severity, detail) in sorted(conditions(health, observed).items(),
                                                         key=lambda kv: kv[1][0] != 'critical')
           if not key.startswith('disk-temp') and key != 'host-health-stale']
    updater = health.get('updater')
    if isinstance(updater, dict) and updater.get('applied'):
        try:
            gap = alert_at - float(updater.get('mtime') or 0)
        except (TypeError, ValueError):
            gap = -1
        if 0 <= gap <= 24 * 3600:
            out.append(f"The image updater applied new versions of {', '.join(updater['applied'])} "
                       f"{max(1, int(gap // 3600))} h before this alert; a fresh image is a common cause of a new failure.")
    errors = health.get('kernel_disk_errors')
    if isinstance(errors, dict) and errors.get('count'):
        out.append('The NAS kernel log has recent disk errors; check the disks before blaming the apps.')
    return out
