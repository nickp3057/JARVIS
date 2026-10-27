#!/usr/bin/python3 -I
"""NAS host health snapshot for JARVIS. Root cron, every 5 minutes.

Writes /volume1/docker/watchdog/host-health.json (mode 644), which the
read-only status key's stack_status.sh includes in its output. Read-only on
the host: it runs `docker ps/inspect`, `smartctl -n standby` (never wakes a
sleeping disk, and only every SMART_EVERY_S), and reads /proc, /sys and two
logs. Nothing here restarts, writes config, or touches containers.

Sections fail independently into ``errors``; the file is always written.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WD = Path('/volume1/docker/watchdog')
OUT = WD / 'host-health.json'
DOCKER = '/usr/bin/docker'
SMARTCTL = '/usr/sbin/smartctl'
KERN_LOG = Path('/var/log/kern.log')
HWMON_LOG = Path('/var/log/hwmon.log')
UPDATER = WD / 'updater_status.txt'
SMART_EVERY_S = 1800
_DISK_ERROR = re.compile(r'I/O error|Buffer I/O error|Medium Error|ata\d+(\.\d+)?: (failed command|exception|error|hard resetting)'
                         r'|md/raid\S*: .*(fail|disabl|degrad)|EXT4-fs error|BTRFS (error|critical)', re.I)
_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def run(argv: list[str], timeout: float = 30) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return done.returncode, done.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def _epoch(text: str) -> int | None:
    """Docker's RFC 3339 with nanoseconds -> epoch seconds."""
    if not text or text.startswith('0001-'):
        return None
    try:
        return int(datetime.fromisoformat(re.sub(r'\.\d+', '', text).replace('Z', '+00:00')).timestamp())
    except ValueError:
        return None


def containers(runner=run) -> list[dict]:
    code, ids = runner([DOCKER, 'ps', '-aq'])
    if code != 0:
        raise RuntimeError('docker ps failed')
    ids = ids.split()
    if not ids:
        return []
    code, raw = runner([DOCKER, 'inspect', *ids], 60)
    if code != 0:
        raise RuntimeError('docker inspect failed')
    out = []
    for item in json.loads(raw):
        state = item.get('State') or {}
        out.append({
            'name': str(item.get('Name', '')).lstrip('/'),
            'image': (item.get('Config') or {}).get('Image', ''),
            'state': state.get('Status', 'unknown'),
            'health': (state.get('Health') or {}).get('Status'),
            'restart_count': int(item.get('RestartCount') or 0),
            'restart_policy': ((item.get('HostConfig') or {}).get('RestartPolicy') or {}).get('Name', ''),
            'started_at': _epoch(state.get('StartedAt', '')),
            'exit_code': state.get('ExitCode'),
            'oom_killed': bool(state.get('OOMKilled')),
        })
    return sorted(out, key=lambda c: c['name'])


def raid(text: str) -> list[dict]:
    arrays, current = [], None
    for line in text.splitlines():
        head = re.match(r'^(md\d+)\s*:\s*(\w+)\s+(\w+)\s+(.*)$', line)
        if head:
            current = {'name': head[1], 'state': head[2], 'level': head[3],
                       'members': re.findall(r'(\w+)\[\d+\]', head[4]), 'slots': None, 'pattern': None,
                       'degraded': False, 'sync': None}
            arrays.append(current)
            continue
        if current is None:
            continue
        slots = re.search(r'\[(\d+)/(\d+)\]\s+\[([U_]+)\]', line)
        if slots:
            current['slots'] = f'{slots[1]}/{slots[2]}'
            current['pattern'] = slots[3]
            current['degraded'] = int(slots[2]) < int(slots[1]) or '_' in slots[3]
        sync = re.search(r'(recovery|resync|reshape|check)\s*=\s*([\d.]+%)', line)
        if sync:
            current['sync'] = f'{sync[1]} {sync[2]}'
    return arrays


def _attr(table: list, attr_id: int) -> int | None:
    for row in table:
        if row.get('id') == attr_id:
            try:
                return int((row.get('raw') or {}).get('value'))
            except (TypeError, ValueError):
                return None
    return None


def smart(device: str, runner=run) -> dict:
    code, raw = runner([SMARTCTL, '-j', '-n', 'standby', '-H', '-A', '-i', f'/dev/{device}'], 40)
    entry = {'device': device, 'checked_at': int(time.time()), 'standby': False}
    try:
        data = json.loads(raw) if raw.strip().startswith('{') else {}
    except ValueError:
        data = {}
    if 'smart_status' not in data:
        # Exit bit 2 means both "open failed" and "-n standby skipped a
        # sleeping disk"; only the message tells them apart.
        notes = ' '.join(str(m.get('string', '')) for m in (data.get('smartctl') or {}).get('messages') or [])
        if re.search(r'standby|sleep', notes, re.I):
            entry['standby'] = True
        else:
            entry['error'] = (notes or f'smartctl exit {code}')[:200]
        return entry
    table = (data.get('ata_smart_attributes') or {}).get('table') or []
    nvme = data.get('nvme_smart_health_information_log') or {}
    entry.update({
        'model': data.get('model_name'),
        'smart_passed': (data.get('smart_status') or {}).get('passed'),
        'temp_c': (data.get('temperature') or {}).get('current'),
        'power_on_hours': (data.get('power_on_time') or {}).get('hours'),
        'reallocated': _attr(table, 5),
        'pending': _attr(table, 197),
        'offline_uncorrectable': _attr(table, 198),
        'crc_errors': _attr(table, 199),
        'media_errors': nvme.get('media_errors'),
    })
    return entry


def disks(previous: list[dict], runner=run, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    old = {d.get('device'): d for d in previous if isinstance(d, dict)}
    out = []
    for path in sorted(glob.glob('/sys/block/sd*') + glob.glob('/sys/block/nvme*')):
        device = os.path.basename(path)
        last = old.get(device)
        if last and not last.get('standby') and now - float(last.get('checked_at') or 0) < SMART_EVERY_S:
            out.append(last)
            continue
        fresh = smart(device, runner)
        if fresh.get('standby') and last:
            # Keep the last real reading; mark that the disk is asleep now.
            fresh = dict(last, standby=True)
        out.append(fresh)
    return out


def volumes() -> list[dict]:
    out = []
    for path in sorted(glob.glob('/volume[0-9]*')):
        if not os.path.ismount(path):
            continue
        st = os.statvfs(path)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        out.append({'path': path, 'total_bytes': total, 'used_bytes': used, 'free_bytes': free,
                    'used_pct': round(100 * used / total, 1) if total else None})
    return out


def cpu_temp() -> float | None:
    temps = []
    for zone in glob.glob('/sys/class/thermal/thermal_zone*/temp'):
        try:
            temps.append(int(Path(zone).read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    return max(temps) if temps else None


def tail(path: Path, size: int = 262144) -> list[str]:
    with path.open('rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - size))
        return f.read().decode('utf-8', errors='replace').splitlines()


def fans(lines: list[str]) -> dict | None:
    for line in reversed(lines):
        match = re.search(r'f1=(-?\d+),f2=(-?\d+)', line)
        if match:
            return {'f1_rpm': int(match[1]), 'f2_rpm': int(match[2]), 'line': _ANSI.sub('', line).strip()[:200]}
    return None


def kernel_disk_errors(lines: list[str]) -> dict:
    hits = [line.strip()[:300] for line in lines if _DISK_ERROR.search(line)]
    return {'count': len(hits), 'recent': hits[-5:]}


def updater(text: str, mtime: float) -> dict:
    applied = re.search(r'^APPLIED:\s*(.+)$', text, re.M)
    last = re.search(r'^Last run:\s*(.+)$', text, re.M)
    return {'last_run': last[1].strip() if last else None, 'mtime': int(mtime),
            'applied': applied[1].split() if applied else [], 'text': text[:2000]}


def collect(previous: dict | None = None, runner=run) -> dict:
    previous = previous or {}
    out: dict = {'generated_at': int(time.time()), 'errors': []}

    def section(name, fn, default=None):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001 - one section must not sink the snapshot
            out[name] = default
            out['errors'].append(f'{name}: {exc.__class__.__name__}: {str(exc)[:160]}')

    section('containers', lambda: containers(runner), [])
    section('raid', lambda: raid(Path('/proc/mdstat').read_text()), [])
    section('disks', lambda: disks(previous.get('disks') or [], runner), previous.get('disks') or [])
    section('volumes', volumes, [])
    section('cpu_temp_c', cpu_temp)
    section('fans', lambda: fans(tail(HWMON_LOG, 8192)))
    section('kernel_disk_errors', lambda: kernel_disk_errors(tail(KERN_LOG)), None)
    section('updater', lambda: updater(UPDATER.read_text()[:4096], UPDATER.stat().st_mtime))
    return out


def main() -> int:
    try:
        previous = json.loads(OUT.read_text())
    except (OSError, ValueError):
        previous = {}
    data = collect(previous if isinstance(previous, dict) else {})
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, separators=(',', ':')))
    os.chmod(temporary, 0o644)
    os.replace(temporary, OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
