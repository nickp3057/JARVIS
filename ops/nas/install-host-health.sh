#!/bin/bash
# Run on NAS as root. Preview is read-only; --apply repeats the diff first.
# Installs host_health.py (root cron, every 5 min) and the stack_status.sh
# that includes its host-health.json. The read-only status key gains output,
# not privilege: no new key, sudo rule, Docker access, or account change.
set -euo pipefail
WD=/volume1/docker/watchdog
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
CRON_LINE='*/5 * * * * /usr/bin/python3 -I /volume1/docker/watchdog/host_health.py'
case ${1:---preview} in --preview|--apply) ;; *) exit 64 ;; esac
bash -n "$HERE/stack_status.sh"
/usr/bin/python3 -I -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$HERE/host_health.py"
for file in stack_status.sh host_health.py; do
    diff -u --label "$WD/$file (current)" --label "$WD/$file (proposed)" "$(test -f "$WD/$file" && echo "$WD/$file" || echo /dev/null)" "$HERE/$file" || [[ $? == 1 ]]
done
echo "Root crontab gains (if missing): $CRON_LINE"
[[ ${1:---preview} == --apply ]] || exit 0
[[ $EUID == 0 ]] || { echo 'Run --apply with sudo.'; exit 77; }
exec 9>"$WD/stack.lock"
flock -n 9 || { echo 'Stack operation in progress; retry later.'; exit 75; }
# Fail closed unless the live status script is one we reviewed: the
# 2026-09-20 version, or this installer's own (so a re-run is harmless).
live=$(sha256sum "$WD/stack_status.sh" | cut -d' ' -f1)
case $live in
    8b5281742aef911ec8f1c495a0a90acd2906bea9177179c31e791240f388f583|16e504643b1612307aa950e038e6537fcb080d4b666b8d3b7d171226bbf56ce3) ;;
    *) echo "Unreviewed change in $WD/stack_status.sh (sha256 $live); stop and review before installing." >&2; exit 1 ;;
esac
BACKUP="$WD/backups/$(date -u +%Y%m%dT%H%M%SZ)-host-health"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
for file in stack_status.sh host_health.py; do
    [[ ! -e "$WD/$file" ]] || cp -a "$WD/$file" "$BACKUP/$file"
done
crontab -l > "$BACKUP/root.cron" 2>/dev/null || : > "$BACKUP/root.cron"
for file in stack_status.sh host_health.py; do
    sed -n '1,$p' "$HERE/$file" > "$WD/$file.new"
    chmod 755 "$WD/$file.new"
    chown root:root "$WD/$file.new"
    mv -f "$WD/$file.new" "$WD/$file"
done
# One root crontab edit from a single sudo, appending only when missing.
cp "$BACKUP/root.cron" "$BACKUP/root.cron.new"
grep -qF 'host_health.py' "$BACKUP/root.cron.new" || echo "$CRON_LINE" >> "$BACKUP/root.cron.new"
crontab "$BACKUP/root.cron.new"
/usr/bin/python3 -I "$WD/host_health.py"
/usr/bin/python3 -I - "$WD/host-health.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"host-health.json: {len(d.get('containers') or [])} containers, {len(d.get('raid') or [])} arrays, "
      f"{len(d.get('disks') or [])} disks, {len(d.get('volumes') or [])} volumes, errors: {d.get('errors') or 'none'}")
PY
if id Jarvis >/dev/null 2>&1 && ! sudo -u Jarvis test -r "$WD/host-health.json"; then
    echo "WARNING: Jarvis cannot read host-health.json; check the docker share permission (README \"Share ACL\")." >&2
fi
crontab -l | grep -F host_health.py
printf 'Installed without restarting anything. Backups: %s\nRollback: restore %s/stack_status.sh, crontab %s/root.cron, rm %s/host_health.py %s/host-health.json\n' \
    "$BACKUP" "$BACKUP" "$BACKUP" "$WD" "$WD"
