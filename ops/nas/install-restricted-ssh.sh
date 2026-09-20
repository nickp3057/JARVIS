#!/bin/bash
# Preview first; root applies only reviewed local files and two public keys.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
WD=/volume1/docker/watchdog
NAS_HOME=$(getent passwd Nick | cut -d: -f6)
[[ -d "$NAS_HOME" ]] || { echo 'Nick home missing; resolve AuthorizedKeysFile first.'; exit 1; }
case ${1:---preview} in --preview|--apply) ;; *) exit 64 ;; esac
for key in jarvis_nas_recovery.pub jarvis_nas_status.pub; do
    [[ $(wc -l < "$HERE/$key") == 1 ]] && ssh-keygen -lf "$HERE/$key"
    [[ $(cut -d' ' -f1 "$HERE/$key") == ssh-ed25519 ]] || exit 1
done
AUTH_LINE='command="/volume1/docker/watchdog/recover_stack.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc '
STATUS_LINE='command="/volume1/docker/watchdog/stack_status.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc '
RULE='Nick ALL=(root) NOPASSWD: /usr/local/libexec/jarvis-nas-launch *'
printf 'Proposed authorized_keys additions:\n%s%s\n%s%s\nSudo rule:\n%s\n' "$AUTH_LINE" "$(cat "$HERE/jarvis_nas_recovery.pub")" "$STATUS_LINE" "$(cat "$HERE/jarvis_nas_status.pub")" "$RULE"
for file in recover_stack.sh stack_status.sh jarvis-nas-launch; do
    DEST="$WD/$file"
    [[ $file != jarvis-nas-launch ]] || DEST=/usr/local/libexec/jarvis-nas-launch
    bash -n "$HERE/$file"
    diff -u --label "$DEST (current)" --label "$DEST (proposed)" "$(test -f "$DEST" && echo "$DEST" || echo /dev/null)" "$HERE/$file" || [[ $? == 1 ]]
done
cat <<'PLAN'
Permission changes (non-recursive): remove extended/default ACLs; root:root
755 on /volume1/docker and watchdog, root:root 644 on compose, root:root 600
on qbit_creds.env; root owns watchdog scripts/lock/state/logs (scripts 755,
status/logs/counters 644, lock 600). Nick home 755; .ssh 700; authorized_keys
600; remove their extended/default ACLs. Existing authorized keys preserved.
Backups include scripts, authorized_keys, sudo rule, credentials, compose,
and getfacl output for restoration. No containers or cron schedules changed.
PLAN
[[ ${1:---preview} == --apply ]] || exit 0
[[ $EUID == 0 ]] || { echo 'Run --apply with sudo.'; exit 77; }
for tool in getfacl setfacl visudo nohup setsid flock; do command -v "$tool" >/dev/null; done
exec 9>"$WD/stack.lock"
flock -n 9 || { echo 'Stack operation in progress; retry later.'; exit 75; }
BACKUP=/var/backups/jarvis-nas/$(date -u +%Y%m%dT%H%M%SZ)-ssh
umask 077
mkdir -p "$BACKUP" /var/lib/jarvis-nas/requests /usr/local/libexec "$NAS_HOME/.ssh"
FILES=(/volume1/docker "$WD" "$WD/docker-compose.yaml" /volume1/docker/docker-compose.yaml "$WD/qbit_creds.env" "$NAS_HOME" "$NAS_HOME/.ssh" "$NAS_HOME/.ssh/authorized_keys")
# Every script is backed up before even permission edits; never copy secrets
# into the repository or a world-readable staging directory.
for path in "$WD"/*.sh "$WD"/stack.lock "$WD"/*.log "$WD"/.*count "$WD"/.last_action "$WD"/.rotate_cooldown "$WD"/.byparr_slow "$WD"/recovery-status.json /usr/local/libexec/jarvis-nas-launch /etc/sudoers.d/jarvis-nas; do
    [[ ! -e "$path" ]] || FILES+=("$path")
done
for path in "${FILES[@]}"; do
    [[ -e "$path" ]] || continue
    getfacl -p "$path" >> "$BACKUP/permissions.acl"
    if [[ -f "$path" ]]; then
        mkdir -p "$BACKUP$(dirname "$path")"
        cp -a "$path" "$BACKUP$path"
    fi
done
printf '%s\n' "$RULE" > "$BACKUP/sudoers.proposed"
visudo -cf "$BACKUP/sudoers.proposed"
# Protect the complete path to the sudo-executable script and Compose input.
for path in /volume1/docker "$WD" /volume1/docker/docker-compose.yaml "$WD/qbit_creds.env"; do
    setfacl -b "$path"
    [[ ! -d "$path" ]] || setfacl -k "$path"
    chown root:root "$path"
done
chmod 755 /volume1/docker "$WD"
chmod 644 /volume1/docker/docker-compose.yaml
chmod 600 "$WD/qbit_creds.env"
for path in "$WD"/*.sh "$WD"/stack.lock "$WD"/*.log "$WD"/.*count "$WD"/.last_action "$WD"/.rotate_cooldown "$WD"/.byparr_slow "$WD"/recovery-status.json; do
    [[ ! -e "$path" ]] || { setfacl -b "$path"; chown root:root "$path"; chmod 644 "$path"; }
done
chmod 755 "$WD"/*.sh
chmod 600 "$WD/stack.lock"
for file in recover_stack.sh stack_status.sh jarvis-nas-launch; do
    DEST="$WD/$file"
    [[ $file != jarvis-nas-launch ]] || DEST=/usr/local/libexec/jarvis-nas-launch
    sed -n '1,$p' "$HERE/$file" > "$DEST.new"
    chmod 755 "$DEST.new"
    chown root:root "$DEST.new"
    mv -f "$DEST.new" "$DEST"
done
# Add keys only if not already present. Existing account access is retained.
touch "$NAS_HOME/.ssh/authorized_keys"
for key in recovery status; do
    PUB=$(cat "$HERE/jarvis_nas_$key.pub")
    if ! grep -Fq "$(echo "$PUB" | cut -d' ' -f2)" "$NAS_HOME/.ssh/authorized_keys"; then
        if [[ $key == recovery ]]; then PREFIX=$AUTH_LINE; else PREFIX=$STATUS_LINE; fi
        printf '\n%s%s\n' "$PREFIX" "$PUB" >> "$NAS_HOME/.ssh/authorized_keys"
    else
        echo "Key already present: $key; inspect its restrictions before enabling Jarvis."
    fi
done
for path in "$NAS_HOME" "$NAS_HOME/.ssh" "$NAS_HOME/.ssh/authorized_keys"; do
    setfacl -b "$path"
    [[ ! -d "$path" ]] || setfacl -k "$path"
    chown Nick:admin "$path"
done
chmod 755 "$NAS_HOME"
chmod 700 "$NAS_HOME/.ssh"
chmod 600 "$NAS_HOME/.ssh/authorized_keys"
install -o root -g root -m 440 "$BACKUP/sudoers.proposed" /etc/sudoers.d/jarvis-nas
visudo -cf /etc/sudoers.d/jarvis-nas
printf 'Installed without restarting anything. Root-only backup: %s\n' "$BACKUP"
