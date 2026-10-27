#!/bin/bash
# Separate read-only forced key; no sudo, command dispatch, or Docker access.
set -euo pipefail
[[ $# == 0 && ${SSH_ORIGINAL_COMMAND:-status} == status ]] || { echo DENIED >&2; exit 64; }
exec /usr/bin/python3 -I - <<'PY'
import json
from pathlib import Path
import time
wd = Path('/volume1/docker/watchdog')
out = {'observed_at': int(time.time()), 'recovery': None, 'watchdog_actions': {}, 'log_tail': {}, 'host_health': None, 'errors': []}
try:
    out['recovery'] = json.loads((wd / 'recovery-status.json').read_text()[:8192])
except FileNotFoundError:
    pass
except (OSError, ValueError):
    out['errors'].append('Recovery status unavailable')
for name in ('.last_action', '.rotate_cooldown'):
    try:
        out['watchdog_actions'][name] = int((wd / name).read_text()[:32])
    except FileNotFoundError:
        out['watchdog_actions'][name] = 0
    except (OSError, ValueError):
        out['errors'].append(name + ' unavailable')
# Written every 5 min by root cron (host_health.py): containers, RAID, SMART,
# volumes, temperatures, updater. Absent until that installer has run.
try:
    out['host_health'] = json.loads((wd / 'host-health.json').read_text()[:262144])
except FileNotFoundError:
    out['host_health'] = None
except (OSError, ValueError):
    out['host_health'] = None
    out['errors'].append('Host health unavailable')
for name in ('watchdog.log', 'indexer_watchdog.log'):
    try:
        with (wd / name).open('rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            out['log_tail'][name] = f.read(8192).decode('utf-8', errors='replace').splitlines()[-20:]
    except OSError:
        out['errors'].append(name + ' unavailable')
print(json.dumps(out))
PY
