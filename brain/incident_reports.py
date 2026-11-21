"""Incident reports: everything JARVIS knows about one alert, gathered on tap.

The companion's "Needs attention" rows call ``POST /api/alerts/{id}/report``.
``build`` collects live evidence over the integrations JARVIS already has
(no new NAS permissions): arr/qBit/Prowlarr health, the read-only NAS status
key (recovery record, watchdog logs), a media doctor sweep, recent warning
and error log lines from each app's own API, and recent changes. It ranks
suspected causes with fixed rules (no model call; the conversation turn that
follows does the reasoning) and stores the lot as a ``debug_reports`` entry
with ``source="incident"``, which ``inspect_debug_report`` already reads.

Read-only. Fixes are proposed in the chat that follows, through the existing
gates (media doctor -> confirm gate, arr restart gate, self-repair ids).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
import os
import re
import time
from typing import Any, Callable

import requests

from brain import debug_reports, event_bus, self_repair
from integrations import home_assistant, media_doctor, nas_host, nas_stack, qbittorrent
from integrations import servarr_common as servarr
from jarvis_logger import get_logger

log = get_logger(__name__)

# The whole report must fit inside the companion's request timeout.
_DEADLINE_S = 25.0
_LOG_LINES = 8
_RELATED_WINDOW_S = 2 * 3600
_API_KEYS = ('qbittorrent-api', 'radarr-api', 'sonarr-api', 'prowlarr-api')
# Shown when the NAS has no host-health.json yet (ops/nas/install-host-health.sh).
_GAPS = ('Container state, restart counts, image updates and disk/SMART health are not visible: '
         'the NAS host-health snapshot is not installed or not readable.')


def find_alert(alert_id: int, created_at: float | None = None) -> event_bus.Alert | None:
    """The bus alert with this id. ``created_at`` guards against a phone
    holding an id from before a jarvis-api restart (ids restart at 1)."""
    for alert in event_bus.since(0, limit=500):
        if alert.id == alert_id:
            if created_at is not None and abs(alert.created_at - created_at) > 2:
                return None
            return alert
    return None


def _clock(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime('%a %H:%M')


def _flat(text: Any, limit: int = 240) -> str:
    return ' '.join(str(text).split())[:limit]


def _ago(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    return f'{minutes} min ago' if minutes < 120 else f'{minutes // 60} h ago'


# --- evidence gatherers (each returns plain lines; failures are lines too) ---

def _arr_logs(service: str) -> list[str]:
    if not servarr.is_configured(service):
        return []
    ok, payload = servarr.request(service, 'GET', '/api/v3/log', params={
        'page': 1, 'pageSize': 50, 'sortKey': 'time', 'sortDirection': 'descending'})
    if not ok:
        return [f'{service.title()} log unavailable: {str(payload)[:160]}']
    return _servarr_records(service.title(), payload)


def _servarr_records(label: str, payload: Any) -> list[str]:
    records = payload.get('records', []) if isinstance(payload, dict) else []
    bad = [r for r in records if isinstance(r, dict) and str(r.get('level', '')).lower() in {'warn', 'error', 'fatal'}]
    return [f"{label} {str(r.get('time', ''))[:19]} {r.get('level', '')}: {_flat(r.get('message', ''))}"
            for r in bad[:_LOG_LINES]]


def _prowlarr_logs() -> list[str]:
    base = os.getenv('PROWLARR_URL', '').strip().rstrip('/')
    key = os.getenv('PROWLARR_API_KEY', '').strip()
    if not base or not key:
        return []
    try:
        response = requests.get(base + '/api/v1/log', headers={'X-Api-Key': key}, timeout=10, allow_redirects=False,
                                params={'page': 1, 'pageSize': 50, 'sortKey': 'time', 'sortDirection': 'descending'})
        response.raise_for_status()
        return _servarr_records('Prowlarr', response.json())
    except (requests.RequestException, ValueError) as exc:
        return [f'Prowlarr log unavailable: {exc.__class__.__name__}']


def _indexer_errors() -> dict[str, str]:
    """The real HTTP status / Cloudflare code behind each backed-off indexer,
    quoted from Prowlarr's log so the turn doesn't guess (2026-10-28)."""
    base = os.getenv('PROWLARR_URL', '').strip().rstrip('/')
    key = os.getenv('PROWLARR_API_KEY', '').strip()
    if not base or not key:
        return {}
    down = nas_stack.indexer_outages(base, key)
    if not down:
        return {}
    response = requests.get(base + '/api/v1/log', headers={'X-Api-Key': key}, timeout=10, allow_redirects=False,
                            params={'page': 1, 'pageSize': 500, 'sortKey': 'time', 'sortDirection': 'descending'})
    response.raise_for_status()
    records = [r for r in response.json().get('records', []) if isinstance(r, dict)]
    return nas_stack.indexer_errors(base, key, records, [int(k.split(':')[1]) for k in down])


def _qbit_logs() -> list[str]:
    if not qbittorrent.is_configured():
        return []
    # type 4 = warning, 8 = critical
    ok, payload = qbittorrent._request('/log/main', params={'normal': 'false', 'info': 'false',
                                                            'warning': 'true', 'critical': 'true'})
    if not ok or not isinstance(payload, list):
        return [f'qBittorrent log unavailable: {str(payload)[:160]}']
    entries = [e for e in payload if isinstance(e, dict)][-_LOG_LINES:]
    return [f"qBittorrent {_clock(float(e.get('timestamp', 0) or 0))} "
            f"{'critical' if e.get('type') == 8 else 'warning'}: {_flat(e.get('message', ''))}" for e in entries]


def _ha_logs() -> list[str]:
    ok, payload = home_assistant._request('GET', '/api/error_log')
    if not ok:
        return [f'Home Assistant log unavailable: {str(payload)[:160]}'] if home_assistant._is_configured() else []
    lines = [line for line in str(payload).splitlines() if re.search(r'\b(ERROR|WARNING|CRITICAL)\b', line)]
    return [f'Home Assistant {_flat(line)}' for line in lines[-_LOG_LINES:]]


def _media_sweep(torrent_hash: str = '') -> list[str]:
    """The media doctor's view: for a stalled download, a full trace of the
    title behind it (library, release, indexers, client) instead of the
    generic queue sweep; the sweep when no arr item owns the torrent."""
    report = None
    if torrent_hash:
        found = media_doctor.find_queue_record(torrent_hash)
        if found is not None:
            service, record, label = found
            # The library title (an episode's label is "Series — Episode", which the library doesn't know).
            owner = record.get('movie' if service == 'radarr' else 'series') or {}
            report = media_doctor.diagnose(str(owner.get('title') or label), service)
    if report is None:
        report = media_doctor.diagnose()
    lines = [str(report.get('summary', ''))]
    lines += [f"{s['stage']}: {s['detail']}" for s in report.get('steps', []) if s.get('status') == 'fail'][:6]
    lines += [f"possible fix: {f['label']}" for f in report.get('fixes', [])[:4]]
    return [line for line in lines if line]


def _download_lines(torrent_hash: str) -> list[str]:
    """The arr queue item behind a stalled torrent, traced by the media doctor."""
    found = media_doctor.find_queue_record(torrent_hash)
    if found is None:
        return ['No Radarr/Sonarr queue item owns this torrent (added by hand, or already removed).']
    service, record, label = found
    report = media_doctor._inspect_queue_record(service, record, label, [], time.time())
    lines = [f"{label} in {service.title()}: {report['summary']}", f"Release: {record.get('title', '?')}"]
    lines += [f"{s['stage']}: {s['detail']}" for s in report['steps']]
    return lines


def _gather(deadline: float, alert: event_bus.Alert | None = None) -> dict[str, Any]:
    jobs: dict[str, Callable[[], Any]] = {
        'health': nas_stack.poll_health,
        'status': nas_stack.watchdog_status if nas_stack.status_configured() else (lambda: None),
        'media': _media_sweep,  # traces the stalled title when the alert names a torrent (below)
        'radarr_log': lambda: _arr_logs('radarr'),
        'sonarr_log': lambda: _arr_logs('sonarr'),
        'prowlarr_log': _prowlarr_logs,
        'indexer_errors': _indexer_errors,
        'qbit_log': _qbit_logs,
        'ha_log': _ha_logs,
    }
    torrent_hash = str((alert.context if alert else {}).get('torrent_hash') or '')
    if torrent_hash:
        jobs['download'] = lambda: _download_lines(torrent_hash)
        jobs['media'] = lambda: _media_sweep(torrent_hash)
    pool = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix='incident')
    futures = {name: pool.submit(job) for name, job in jobs.items()}
    wait(futures.values(), timeout=max(1.0, deadline - time.monotonic()))
    pool.shutdown(wait=False, cancel_futures=True)
    out: dict[str, Any] = {}
    for name, future in futures.items():
        if not future.done():
            out[name] = TimeoutError('timed out')
            continue
        try:
            out[name] = future.result()
        except Exception as exc:  # noqa: BLE001 - one broken source must not sink the report
            log.warning('Incident evidence %s failed: %s', name, exc)
            out[name] = exc
    return out


# --- analysis ---------------------------------------------------------------

def suspected_causes(health: dict | None, status: tuple[bool, Any] | None, watchdog_tail: list[str],
                     now: float | None = None, alert_at: float | None = None,
                     indexer_errors: dict[str, str] | None = None) -> list[str]:
    """Ranked, plain-English guesses from fixed rules. Never empty.

    ``indexer_errors`` (``nas_stack.indexer_errors``) replaces the generic
    indexer guess with the HTTP status and Cloudflare code Prowlarr logged."""
    now = time.time() if now is None else now
    issues = (health or {}).get('issues') or {}
    status_ok = bool(status and status[0] and isinstance(status[1], dict))
    data = status[1] if status_ok else {}
    recovery = data.get('recovery') if isinstance(data.get('recovery'), dict) else None
    causes: list[str] = []
    apis_down = [k for k in _API_KEYS if k in issues]
    tail = '\n'.join(watchdog_tail[-40:]).lower()

    if recovery and nas_stack.recovery_active(status, now):
        causes.append(f"A NAS recovery ({recovery.get('source', 'unknown')}) is {recovery.get('state', 'running')} "
                      f"at phase {recovery.get('phase', '?')}; failures during it are expected. Wait for it to finish.")
    if len(apis_down) == len(_API_KEYS):
        if status is not None and not status_ok:
            causes.append('The NAS itself is unreachable (every app API and the status SSH are down): '
                          'power, network, or the NAS is rebooting.')
        else:
            causes.append('qBittorrent, Radarr, Sonarr and Prowlarr are all unreachable while the NAS answers: '
                          'they share gluetun\'s network, so gluetun is most likely down or restarting.')
    elif apis_down:
        causes.append('Unreachable: ' + ', '.join(k.removesuffix('-api') for k in apis_down)
                      + '. The container may have crashed or be restarting.')
    if recovery and recovery.get('state') == 'failed':
        try:
            age = float(data.get('observed_at') or now) - float(recovery.get('updated_at') or 0)
        except (TypeError, ValueError):
            age = 0
        if 0 <= age <= 6 * 3600:
            causes.append(f"The last NAS recovery ({recovery.get('source', 'unknown')}) failed at "
                          f"{recovery.get('phase', '?')} {_ago(age)} (gluetun {recovery.get('gluetun_health', 'unknown')}, "
                          f"DHT {recovery.get('dht_nodes', '?')}), so the automatic fix did not take.")
    dht = (health or {}).get('dht_nodes')
    if dht == 0 or 'qbittorrent-connection' in issues:
        if 'i/o timeout' in tail or 'failed to pass the healthcheck' in tail:
            causes.append('The VPN tunnel is up but carries no traffic (gluetun health checks time out on DNS). '
                          'Usually the VPN provider or upstream internet; a new VPN server often fixes it.')
        else:
            causes.append('qBittorrent has no DHT peers or reports disconnected: the VPN tunnel is likely down.')
    indexers = [k for k in issues if k.startswith('indexer:')]
    quoted = [indexer_errors[k] for k in indexers if indexer_errors and k in indexer_errors]
    if quoted:
        causes.append(f'{len(indexers)} Prowlarr indexer(s) in failure backoff. Prowlarr logged: '
                      + '; '.join(quoted[:3]) + '.')
    elif indexers:
        causes.append(f'{len(indexers)} Prowlarr indexer(s) in failure backoff: '
                      + '; '.join(issues[k] for k in indexers[:3])
                      + '. No HTTP error for it in Prowlarr\'s recent log; often clears alone, and re-saving '
                        'the indexer in Prowlarr resets it.')
    if 'prowlarr-health' in issues and 'proxy' in issues['prowlarr-health'].lower():
        causes.append('Prowlarr reports its indexer proxy (Byparr) unavailable; it may still be cold-starting.')
    for service in ('radarr', 'sonarr'):
        if service + '-health' in issues:
            causes.append(issues[service + '-health'])
    host = nas_host.from_status(status)
    if host:
        causes += nas_host.causes(host[0], host[1], now if alert_at is None else alert_at)
    if not causes:
        causes.append('Nothing is failing right now; the condition behind this alert seems to have cleared.')
    return causes


def _nas_changes(data: dict, now: float) -> list[str]:
    lines: list[str] = []
    observed = float(data.get('observed_at') or now)
    for name, label in (('.last_action', 'DHT watchdog restarted qBittorrent'),
                        ('.rotate_cooldown', 'Indexer watchdog rotated the VPN exit')):
        stamp = float((data.get('watchdog_actions') or {}).get(name) or 0)
        if stamp and observed - stamp <= 48 * 3600:
            lines.append(f'{label} {_ago(observed - stamp)}.')
    recovery = data.get('recovery')
    if isinstance(recovery, dict) and recovery.get('updated_at'):
        age = observed - float(recovery['updated_at'])
        if age <= 48 * 3600:
            lines.append(f"Shared recovery ({recovery.get('source')}) {recovery.get('state')} at "
                         f"{recovery.get('phase')} {_ago(age)}.")
    return lines


def _recent_changes(alert: event_bus.Alert, status: tuple[bool, Any] | None, now: float) -> list[str]:
    lines: list[str] = []
    if status and status[0] and isinstance(status[1], dict):
        try:
            lines += _nas_changes(status[1], now)
        except (TypeError, ValueError, AttributeError):
            lines.append('NAS status returned unexpected data.')
    elif status is not None:
        lines.append(f'NAS status SSH unavailable: {status[1]}')
    try:
        repairs = [p for p in self_repair._load() if p.get('finished_at') and now - float(p['finished_at']) <= 48 * 3600]
    except (TypeError, ValueError):
        repairs = []
    lines += [f"JARVIS repair {p['id']} on {p.get('tool')}: {p.get('status')} {_ago(now - float(p['finished_at']))}."
              for p in repairs[-3:]]
    related = [a for a in event_bus.since(0, limit=500)
               if a.id != alert.id and abs(a.created_at - alert.created_at) <= _RELATED_WINDOW_S
               and (a.kind == alert.kind or a.source == alert.source)]
    lines += [f'Related alert {_clock(a.created_at)} [{a.severity}] {a.title}: {a.message[:200]}' for a in related[-6:]]
    if nas_host.from_status(status) is None:
        lines.append(_GAPS)
    return lines


def _lines(value: Any) -> list[str]:
    if isinstance(value, Exception):
        return [f'unavailable ({value})']
    return list(value or [])


def _sentence(line: str) -> str:
    line = ' '.join(line.split())
    return line if line.endswith(('.', '?', '!')) else line + '.'


def build(alert: event_bus.Alert, *, session_id: str | None = None) -> dict[str, Any]:
    """Gather, analyse and store one incident report; return what the app shows."""
    now = time.time()
    evidence = _gather(time.monotonic() + _DEADLINE_S, alert)
    health = evidence['health'] if isinstance(evidence['health'], dict) else None
    status = evidence['status'] if isinstance(evidence['status'], tuple) else (
        None if evidence['status'] is None else (False, str(evidence['status'])))
    tails = (status[1].get('log_tail') or {}) if status and status[0] and isinstance(status[1], dict) else {}
    watchdog_tail = [str(x) for x in tails.get('watchdog.log', [])] + [str(x) for x in tails.get('indexer_watchdog.log', [])]
    indexer_errors = evidence.get('indexer_errors') if isinstance(evidence.get('indexer_errors'), dict) else {}
    causes = suspected_causes(health, status, watchdog_tail, now, alert.created_at, indexer_errors)
    host = nas_host.from_status(status)

    if health is None:
        health_lines = [f"health poll unavailable ({evidence['health']})"]
    else:
        health_lines = [f'{k}: {v}' for k, v in (health.get('issues') or {}).items()] or ['All monitored arr checks pass.']
        health_lines.append(f"qBittorrent DHT nodes: {health.get('dht_nodes')}, connection {health.get('connection_status')}")
    app_logs = (_lines(evidence['radarr_log']) + _lines(evidence['sonarr_log']) + _lines(evidence['prowlarr_log'])
                + _lines(evidence['qbit_log']) + _lines(evidence['ha_log']))
    if 'download' in evidence:
        target = 'retry_download:' + str(alert.context.get('torrent_hash', '')).lower()[:16]
        retry = [r for r in self_repair.public_proposals() if r['status'] == 'pending' and r.get('target') == target]
        download = _lines(evidence['download'])
        if retry:
            download.append(f"Retry staged as repair {retry[0]['id']} (Home → Repairs → Approve, or say yes when I propose it).")
        causes.insert(0, 'This download is stuck in qBittorrent (no seeders/peers for the release, usually). '
                         'Blocklisting that release and searching again is the usual fix.')
    sections = [
        {'title': 'Suspected cause (rule-based, verify before acting)', 'lines': causes},
        *([{'title': 'This download', 'lines': download}] if 'download' in evidence else []),
        {'title': 'Live health now', 'lines': health_lines},
        *([{'title': "Indexer errors (quoted from Prowlarr's log)", 'lines': list(indexer_errors.values())}]
          if indexer_errors else []),
        {'title': 'Media pipeline', 'lines': _lines(evidence['media'])},
        {'title': 'NAS host (containers, disks, RAID, volumes)',
         'lines': nas_host.describe(*host) if host else ['not available']},
        {'title': 'Recent changes', 'lines': _recent_changes(alert, status, now)},
        {'title': 'NAS watchdog log (tail)', 'lines': watchdog_tail[-12:] or ['not available']},
        {'title': 'App warnings and errors', 'lines': app_logs or ['No recent warnings or errors in the app logs.']},
    ]
    issue = f'{alert.title}: {alert.message}'.strip(': ')
    stored = debug_reports.create_debug_report(
        issue=issue, messages=[], session_id=session_id, sections=sections, source='incident',
        conversation_title=f'Incident: {alert.title}'[:200],
        app_context={'alert_id': str(alert.id), 'kind': alert.kind, 'severity': alert.severity,
                     'alert_source': alert.source, 'raised_at': datetime.fromtimestamp(alert.created_at).isoformat(timespec='seconds')},
    )
    failing = [line for line in health_lines if ':' in line and not line.startswith('qBittorrent DHT')][:4]
    prompt_lines = [f'Incident report {stored["id"]}: {alert.title} ({_clock(alert.created_at)})']
    if alert.message:
        prompt_lines.append(alert.message[:300])
    if failing:
        prompt_lines.append('Failing now: ' + '; '.join(failing))
    prompt_lines.append(f'Suspected cause: {causes[0]}')
    if indexer_errors:
        prompt_lines.append("Indexer errors from Prowlarr's log (quote these, don't guess a different cause): "
                            + '; '.join(indexer_errors.values()))
    from brain import auto_heal
    from brain import self_repair as repairs
    link = auto_heal.link_for(alert.kind, alert.context)
    repair = repairs.latest_for_link(link, alert.created_at - 1800) if link else None
    if repair:
        who = 'Autopilot' if repair['auto'] else 'A repair'
        prompt_lines.append(f"{who} already ran {repair['label']} ({repair['status']}"
                            + (f": {_flat(repair['result'], 200)}" if repair['result'] else '') + ')')
    prompt_lines.append('Read the full report (logs, recent changes), tell me what you think is wrong, and propose a fix.')
    return {
        'report_id': stored['id'],
        'title': alert.title,
        'suspected_causes': causes,
        'sections': sections,
        # One paragraph: in Chat's composer a double tap selects it all.
        'prompt': ' '.join(_sentence(line) for line in prompt_lines),
    }
