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
from typing import Callable, NamedTuple
import uuid

import requests

from integrations import home_assistant, nas_stack, plex


class Fix(NamedTuple):
    label: str
    run: Callable[[], tuple[bool, str]]  # (succeeded and verified, plain-English outcome)


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


FIXES: dict[str, Fix] = {
    'restart_home_assistant': Fix('Restart Home Assistant (homeassistant.restart), then wait until it answers', _restart_home_assistant),
    'retest_indexers': Fix('Re-test every Prowlarr indexer, then check none is still backed off', _retest_indexers),
    'refresh_plex': Fix('Rescan the Plex movie and TV libraries', _refresh_plex),
    'arr_recovery': Fix('Recreate gluetun and the arr containers (the NAS recovery chain; active downloads disconnect), '
                        'then report each service up / starting / failed', _arr_recovery),
}


def run(name: str) -> tuple[bool, str]:
    fix = FIXES.get(name)
    if fix is None:
        return False, f"'{name}' is not an allowlisted stack fix."
    try:
        return fix.run()
    except Exception as exc:  # noqa: BLE001 - report, never crash the worker
        return False, f'{fix.label} failed: {exc.__class__.__name__}: {str(exc)[:200]}'
