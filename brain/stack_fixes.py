"""Tier-2 stack fixes: a short allowlist, each with a post-fix health check.

JARVIS can only pick a name from ``FIXES``; it never composes a command. A
fix runs only through ``brain.self_repair`` after Nick's own approval (his
"yes"/code via ``brain.confirm_gate`` in chat, or the Approve tap in the app),
never on the model's say-so and never automatically (Nick's rule,
2026-10-27). Everything here uses permissions JARVIS already has: Home
Assistant's API, Prowlarr's API, Plex's API, and the existing restricted
recovery key through ``brain.arr_stack``.

Restarts cannot be rolled back; "verify" means re-checking health afterwards
and saying plainly if it did not come back.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, NamedTuple
import uuid

import requests

from integrations import home_assistant, media_doctor, nas_stack, plex


class Fix(NamedTuple):
    label: str
    run: Callable[[dict[str, Any]], tuple[bool, str]]  # params -> (succeeded and verified, outcome)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _restart_home_assistant() -> tuple[bool, str]:
    ok, detail = home_assistant.call_service('homeassistant', 'restart')
    if not ok:
        return False, f'Home Assistant refused the restart: {str(detail)[:200]}'
    _sleep(20)  # it answers for a few seconds before it goes down
    deadline = time.monotonic() + float(os.getenv('STACK_FIX_HA_VERIFY_S', '180'))
    while time.monotonic() < deadline:
        up, _ = home_assistant.test_connection()
        if up:
            return True, 'Home Assistant restarted and is answering again.'
        _sleep(10)
    return False, 'Home Assistant was restarted but is still not answering; check the container on the NAS.'


def _prowlarr() -> tuple[str, str]:
    return os.getenv('PROWLARR_URL', '').strip().rstrip('/'), os.getenv('PROWLARR_API_KEY', '').strip()


def _retest_indexers() -> tuple[bool, str]:
    base, key = _prowlarr()
    if not base or not key:
        return False, 'Prowlarr URL or API key is not configured.'
    try:
        response = requests.post(base + '/api/v1/indexer/testall', headers={'X-Api-Key': key},
                                 timeout=120, allow_redirects=False)
    except requests.RequestException as exc:
        return False, f'Prowlarr unreachable: {exc.__class__.__name__}'
    # testall answers 400 when any indexer fails its test; the status check
    # below is the real verdict either way.
    if response.status_code not in (200, 400):
        return False, f'Prowlarr test-all failed (HTTP {response.status_code}).'
    _sleep(5)
    down = nas_stack.indexer_outages(base, key)
    if down is None:
        return False, 'Indexers re-tested, but Prowlarr status could not be read afterwards.'
    if down:
        return False, 'Indexers re-tested; still backed off: ' + '; '.join(list(down.values())[:4])
    return True, 'Every Prowlarr indexer passed its test and none is backed off.'


def find_indexer(name: str) -> tuple[int, str] | None:
    """(id, name) of the Prowlarr indexer whose name matches, or None."""
    base, key = _prowlarr()
    if not base or not key or not name.strip():
        return None
    response = requests.get(base + '/api/v1/indexer', headers={'X-Api-Key': key}, timeout=10, allow_redirects=False)
    response.raise_for_status()
    wanted = name.strip().lower()
    indexers = [(x['id'], str(x.get('name', ''))) for x in response.json() if isinstance(x, dict) and type(x.get('id')) is int]
    exact = [i for i in indexers if i[1].lower() == wanted]
    partial = [i for i in indexers if wanted in i[1].lower()]
    return (exact or partial or [None])[0]


def _failure(response: requests.Response) -> str:
    try:
        body = response.json()
        text = ' '.join(str(x.get('errorMessage', '')) for x in body if isinstance(x, dict)) if isinstance(body, list) else str(body)
    except ValueError:
        text = response.text
    return nas_stack.explain_http_error(text) or ' '.join(text.split())[:160] or f'HTTP {response.status_code}'


def _switch_indexer_mirror(params: dict[str, Any]) -> tuple[bool, str]:
    """Try each of Prowlarr's offered mirrors with a test that saves nothing;
    save the first that passes. Found 2026-10-28: 1337x.to was Cloudflare
    IP-banned (1006) for the VPN exit while 1337x.st still answered."""
    base, key = _prowlarr()
    if not base or not key:
        return False, 'Prowlarr URL or API key is not configured.'
    indexer_id = params.get('indexer_id')
    if type(indexer_id) is not int:
        return False, 'No indexer recorded for this mirror switch.'
    headers = {'X-Api-Key': key}
    url = f'{base}/api/v1/indexer/{indexer_id}'
    response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
    response.raise_for_status()
    indexer = response.json()
    name = indexer.get('name', f'indexer {indexer_id}')
    field = next((f for f in indexer.get('fields') or [] if f.get('name') == 'baseUrl'), None)
    mirrors = [str(u) for u in indexer.get('indexerUrls') or []]
    if field is None or len(mirrors) < 2:
        return False, f'{name} has no alternative mirrors in Prowlarr.'
    current = field.get('value') or mirrors[0]
    tried: list[str] = []
    for mirror in [m for m in mirrors if m.rstrip('/') != str(current).rstrip('/')]:
        field['value'] = mirror
        test = requests.post(base + '/api/v1/indexer/test', headers=headers, json=indexer,
                             timeout=float(os.getenv('STACK_FIX_MIRROR_TEST_S', '90')), allow_redirects=False)
        if test.status_code != 200:
            tried.append(f'{mirror}: {_failure(test)}')
            continue
        saved = requests.put(url, headers=headers, json=indexer, timeout=30, allow_redirects=False)
        if saved.status_code not in (200, 202):
            return False, f'{name} passed its test on {mirror}, but Prowlarr refused the save (HTTP {saved.status_code}).'
        _sleep(3)
        down = nas_stack.indexer_outages(base, key)
        still = ' It still shows as backed off; re-save it in the Prowlarr UI.' if down and f'indexer:{indexer_id}' in down else ''
        skipped = f" Skipped: {'; '.join(tried)}." if tried else ''
        return not still, f'{name} now uses {mirror} (was {current}); it passed its test.{skipped}{still}'
    return False, (f"No {name} mirror passed, so nothing was saved ({current} kept). " + '; '.join(tried)
                   + '. Every mirror refusing usually means the ban is on the VPN exit IP: a new gluetun server is the fix.')


def _refresh_plex() -> tuple[bool, str]:
    if not plex.is_configured():
        return False, 'Plex is not configured.'
    reply = plex.refresh_libraries()
    return ('rescanning' in reply or 'rescan started' in reply), reply


def _arr_recovery() -> tuple[bool, str]:
    from brain import arr_stack
    if not nas_stack.enabled('ARR_STACK_RESTART_ENABLED'):
        return False, 'Arr stack restart is disabled (ARR_STACK_RESTART_ENABLED).'
    # arr_stack owns the single-flight lock and the asynchronous
    # up / still starting / failed follow-up; nothing is duplicated here.
    started, reply = arr_stack._launch(str(uuid.uuid4()))
    return started, reply


def _retry_download(params: dict[str, Any]) -> tuple[bool, str]:
    if not params.get('service') or params.get('queue_id') is None:
        return False, 'No queue item recorded for this retry.'
    return media_doctor.retry_release(params, wait_s=float(os.getenv('STACK_FIX_RETRY_VERIFY_S', '90')), sleep=_sleep)


FIXES: dict[str, Fix] = {
    'restart_home_assistant': Fix('Restart Home Assistant (homeassistant.restart), then wait until it answers',
                                  lambda _: _restart_home_assistant()),
    'retest_indexers': Fix('Re-test every Prowlarr indexer, then check none is still backed off', lambda _: _retest_indexers()),
    # Params: indexer_id, label (find_indexer).
    'switch_indexer_mirror': Fix("Point a Prowlarr indexer at the first of its mirrors that passes a test (nothing "
                                 'is saved until one does)', _switch_indexer_mirror),
    'refresh_plex': Fix('Rescan the Plex movie and TV libraries', lambda _: _refresh_plex()),
    'arr_recovery': Fix('Recreate gluetun and the arr containers (the NAS recovery chain; active downloads disconnect), '
                        'then report each service up / starting / failed', lambda _: _arr_recovery()),
    # Params come from the Radarr/Sonarr queue record (media_doctor.retry_params).
    'retry_download': Fix('Retry a stuck download: remove and blocklist the release, search again, '
                          'then check a new one was grabbed', _retry_download),
}


def describe(name: str, params: dict[str, Any] | None = None) -> str:
    """The label Nick sees; a retry names the title."""
    if name == 'retry_download' and params and params.get('label'):
        return f"Retry {params['label']}: blocklist the stuck release and search for another"
    if name == 'switch_indexer_mirror' and params and params.get('label'):
        return f"Switch {params['label']} to the first mirror that passes Prowlarr's test (nothing saved until one does)"
    fix = FIXES.get(name)
    return fix.label if fix else f"stack fix '{name}'"


def run(name: str, params: dict[str, Any] | None = None) -> tuple[bool, str]:
    fix = FIXES.get(name)
    if fix is None:
        return False, f"'{name}' is not an allowlisted stack fix."
    try:
        return fix.run(params or {})
    except Exception as exc:  # noqa: BLE001 - report, never crash the worker
        return False, f'{fix.label} failed: {exc.__class__.__name__}: {str(exc)[:200]}'
