"""Read-only arr health, restricted SSH transport, and existing HA delivery."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

import requests

from integrations import home_assistant, qbittorrent, servarr_common


def seconds(name: str, default: float, minimum: float = 30) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return max(minimum, min(value, 86400)) if value == value else default
    except ValueError:
        return default


def count(name: str, default: int, minimum: int = 1, maximum: int = 100) -> int:
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except ValueError:
        return default


def enabled(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def network_status() -> tuple[bool, dict[str, Any]]:
    ok, info = qbittorrent.transfer_info()
    if not ok or not isinstance(info, dict):
        return False, {'error': 'qBittorrent API unreachable, authentication failed, or invalid response'}
    info = dict(info)
    nodes = info.get('dht_nodes')
    if type(nodes) is not int or nodes < 0:
        ok, sync = qbittorrent._request('/sync/maindata')
        state = sync.get('server_state') if ok and isinstance(sync, dict) else None
        nodes = state.get('dht_nodes') if isinstance(state, dict) else None
    info['dht_nodes'] = nodes if type(nodes) is int and nodes >= 0 else None
    return True, info


_INDEXER_STATUS_SOURCES = frozenset({'IndexerStatusCheck', 'IndexerLongTermStatusCheck'})
# Indexer names change only when Nick edits Prowlarr; one list call per 15 min
# instead of one per poll.
_indexer_names: dict[str, Any] = {'at': 0.0, 'base': '', 'names': {}}


def _is_indexer_status_health(item: dict) -> bool:
    source = str(item.get('source', ''))
    if source:
        return source in _INDEXER_STATUS_SOURCES
    return str(item.get('message', '')).lower().startswith(('indexers unavailable due to failures', 'all indexers are unavailable due to failures'))


def _parse_utc(value: Any) -> float | None:
    """Parse a .NET ISO timestamp (7 fractional digits, trailing Z)."""
    if not isinstance(value, str) or not value:
        return None
    text = re.sub(r'\.\d+', '', value.strip()).replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _indexer_name_map(base: str, key: str) -> dict[int, str]:
    now = time.monotonic()
    if _indexer_names['base'] == base and now - _indexer_names['at'] < 900 and _indexer_names['names']:
        return _indexer_names['names']
    response = requests.get(base + '/api/v1/indexer', headers={'X-Api-Key': key}, timeout=10, allow_redirects=False)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError('invalid indexer list')
    names = {int(x['id']): str(x.get('name') or f"indexer {x['id']}") for x in payload
             if isinstance(x, dict) and type(x.get('id')) is int and x.get('enable', True)}
    _indexer_names.update(at=now, base=base, names=names)
    return names


def indexer_outages(base: str, key: str, now: float | None = None) -> dict[str, str] | None:
    """Per-indexer down conditions from Prowlarr, or None if unavailable.

    An indexer is down while Prowlarr's failure backoff (``disabledTill``) is
    in the future. Disabled-by-Nick indexers are ignored. None means "could
    not tell" and callers keep the aggregate health line as the fallback."""
    now = time.time() if now is None else now
    try:
        response = requests.get(base + '/api/v1/indexerstatus', headers={'X-Api-Key': key}, timeout=10, allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or any(not isinstance(x, dict) or type(x.get('indexerId')) is not int for x in payload):
            return None
        down = [(x['indexerId'], until) for x in payload if (until := _parse_utc(x.get('disabledTill'))) is not None and until > now]
        if not down:
            return {}
        names = _indexer_name_map(base, key)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return None
    issues: dict[str, str] = {}
    for indexer_id, until in down:
        if indexer_id not in names:
            continue  # disabled by Nick or deleted since the name refresh
        retry = datetime.fromtimestamp(until).strftime('%H:%M')
        issues[f'indexer:{indexer_id}'] = f'Indexer {names[indexer_id]} is down (Prowlarr backed it off after failures; next retry {retry})'
    return issues


def poll_health() -> dict[str, Any]:
    """One bounded polling cycle. No model calls and no recovery actions."""
    issues: dict[str, str] = {}
    ok, qbit = network_status()
    if not ok:
        issues['qbittorrent-api'] = qbit['error']
    elif qbit['dht_nodes'] is None:
        issues['qbittorrent-dht-unavailable'] = 'qBittorrent DHT count unavailable'
    if ok and qbit.get('connection_status') == 'disconnected':
        issues['qbittorrent-connection'] = 'qBittorrent reports disconnected'
    for service in ('radarr', 'sonarr'):
        healthy, payload = servarr_common.get_health(service)
        if not healthy or not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
            issues[service + '-api'] = f'{service.title()} API unreachable, authentication failed, or invalid health response'
            continue
        warnings = [str(x.get('message') or x.get('type') or 'health error')[:220] for x in payload if str(x.get('type', 'error')).lower() != 'ok']
        if warnings:
            issues[service + '-health'] = f'{service.title()} health: ' + '; '.join(warnings[:3])
    # Prowlarr v1 health when a key exists; public /ping otherwise. Byparr's
    # :8191 is intentionally not published on the host (only Prowlarr reaches
    # it, over the internal Docker network) — do not add a direct check for
    # it here. Prowlarr's own health list is the only supported signal for a
    # down Byparr/indexer-proxy from outside the stack, so a failure there is
    # treated as critical the same way a down Byparr would have been.
    base = os.getenv('PROWLARR_URL', '').strip().rstrip('/')
    if not base:
        issues['prowlarr-api'] = 'Prowlarr monitoring URL is not configured'
    else:
        key = os.getenv('PROWLARR_API_KEY', '').strip()
        path = '/api/v1/health' if key else '/ping'
        try:
            response = requests.get(base + path, headers={'X-Api-Key': key} if key else {}, timeout=10, allow_redirects=False)
            response.raise_for_status()
            payload = response.json()
            if not 200 <= response.status_code < 300:
                raise ValueError('redirect')
            if key:
                if not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
                    raise ValueError('invalid health')
                down = indexer_outages(base, key)
                if down is not None:
                    # Per-indexer conditions replace Prowlarr's aggregate
                    # "Indexers unavailable due to failures: a, b, c" line, so
                    # each indexer alerts and recovers on its own.
                    issues.update(down)
                    payload = [x for x in payload if not _is_indexer_status_health(x)]
                if payload:
                    issues['prowlarr-health'] = 'Prowlarr health: ' + '; '.join(str(x.get('message', 'health error'))[:220] for x in payload[:3])
            elif not isinstance(payload, dict) or str(payload.get('status', '')).lower() != 'ok':
                raise ValueError('invalid readiness')
        except (requests.RequestException, ValueError):
            issues['prowlarr-api'] = 'Prowlarr API unreachable or readiness check failed'
    return {'issues': issues, 'dht_nodes': qbit.get('dht_nodes'), 'connection_status': qbit.get('connection_status', 'unknown')}


def _ssh(kind: str, command: str) -> tuple[bool, Any]:
    host = os.getenv('NAS_SSH_HOST', '').strip()
    user = os.getenv('NAS_SSH_USER', 'Nick').strip()
    key = os.getenv(f'NAS_{kind.upper()}_SSH_KEY', '').strip()
    hosts = os.getenv('NAS_SSH_KNOWN_HOSTS', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', host) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', user):
        return False, 'NAS SSH host/user is not configured correctly'
    if not key or not hosts or not Path(key).is_file() or not Path(hosts).is_file():
        return False, 'NAS restricted key or pinned known-hosts file is missing'
    argv = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'IdentityAgent=none', '-o', 'StrictHostKeyChecking=yes', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=5',
            '-o', 'ServerAliveCountMax=2', '-o', f'UserKnownHostsFile={hosts}', '-i', key,
            f'{user}@{host}', command]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, 'NAS SSH unavailable or timed out; outcome unknown'
    if result.returncode:
        if 'account is currently not available' in (result.stderr or ''):
            # sshd runs forced commands via the login shell; UGOS resets the
            # Jarvis account to nologin whenever it is saved in its UI.
            return False, ('NAS Jarvis account shell was reset to nologin (UGOS account save), so no restricted '
                           'key can run; see ops/nas/README.md "Shell reset"')
        return False, f'NAS restricted SSH failed (exit {result.returncode}); no success confirmed'
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return False, 'NAS returned invalid status; outcome unknown'
    return (True, data) if isinstance(data, dict) else (False, 'NAS returned invalid status')


def watchdog_status() -> tuple[bool, Any]:
    return _ssh('status', 'status')


def status_configured() -> bool:
    """True when the read-only status key and pinned host file are set up.

    Lets callers skip the SSH round trip entirely (and drop the "activity
    unknown" suffix) instead of reporting a permanent unknown."""
    key = os.getenv('NAS_STATUS_SSH_KEY', '').strip()
    hosts = os.getenv('NAS_SSH_KNOWN_HOSTS', '').strip()
    return bool(os.getenv('NAS_SSH_HOST', '').strip() and key and hosts and Path(key).is_file() and Path(hosts).is_file())


def recovery_active(status: tuple[bool, Any] | None, now: float | None = None) -> bool:
    """True while the NAS reports a recovery running or just finished.

    Covers the NAS's own DHT/indexer watchdog recoveries, which recreate
    every container and used to page Nick with a one-minute "unreachable"
    blip. ``ARR_STACK_MAINTENANCE_SETTLE_S`` (default 300) extends the hold
    past completion while apps finish starting."""
    if not status or not status[0] or not isinstance(status[1], dict):
        return False
    recovery = status[1].get('recovery')
    if not isinstance(recovery, dict):
        return False
    try:
        observed = float(status[1].get('observed_at') or (now if now is not None else time.time()))
        updated = float(recovery.get('updated_at') or 0)
    except (TypeError, ValueError):
        return False
    age = observed - updated
    if recovery.get('state') == 'running':
        # A running record untouched for 15 min is a crashed job, not maintenance.
        return 0 <= age <= 900
    return 0 <= age <= seconds('ARR_STACK_MAINTENANCE_SETTLE_S', 300, 0)


def start_recovery(request_id: str) -> tuple[bool, Any]:
    if not re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', request_id):
        return False, 'Invalid recovery request ID'
    return _ssh('recovery', 'recover ' + request_id)


def watchdog_note(status: tuple[bool, Any] | None = None) -> str:
    ok, data = status if status is not None else watchdog_status()
    if not ok or not isinstance(data, dict):
        return 'NAS watchdog activity: unknown (read-only SSH unavailable).'
    try:
        now = float(data['observed_at'])
        actions = data['watchdog_actions']
        recent = [name for name, stamp in actions.items() if stamp and 0 <= now - float(stamp) <= 3600]
        recovery = data.get('recovery') or {}
        parts = ['NAS watchdog acted within the last hour: ' + ', '.join('DHT' if x == '.last_action' else 'indexer' for x in recent) + '.' if recent else 'No recorded NAS watchdog action in the last hour.']
        if recovery:
            age = max(0, int(now - float(recovery['updated_at'])))
            parts.append(f"Last shared recovery: {recovery.get('source', 'unknown')}, {recovery.get('state', 'unknown')}, {age // 60} min ago.")
        if data.get('errors'):
            parts.append('Some watchdog files were unavailable; activity may be incomplete.')
        return ' '.join(parts)
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return 'NAS watchdog activity: unknown (invalid status).'


def notify_phone(message: str) -> bool:
    service = os.getenv('ARR_STACK_NOTIFY_SERVICE', '').strip().removeprefix('notify.')
    if not re.fullmatch(r'mobile_app_[a-z0-9_]+', service):
        return False
    ok, _ = home_assistant.call_service('notify', service, service_data={'title': 'Jarvis — arr stack', 'message': message[:1800]})
    return ok


def ping_deadman() -> bool:
    url = os.getenv('JARVIS_HEALTHCHECKS_URL', '').strip()
    parsed = urlsplit(url)
    # Prevent accidentally publishing the secret ping URL or sending it to a
    # redirect. Account/notification integrations are managed in Healthchecks.
    if parsed.scheme != 'https' or parsed.hostname not in {'hc-ping.com', 'ping.healthchecks.io'} or parsed.username or parsed.password:
        return False
    try:
        response = requests.get(url, timeout=10, allow_redirects=False)
        return response.status_code == 200 and response.text.strip() == 'OK'
    except requests.RequestException:
        return False
