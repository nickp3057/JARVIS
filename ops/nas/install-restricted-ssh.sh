#!/bin/bash
# Preview first; root applies only reviewed local files and two public keys.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
WD=/volume1/docker/watchdog
# The restricted keys live on a dedicated, non-admin account. UGOS's global
# ForceCommand (see sshd-jarvis.conf) gives admin/root-group logins an
# arbitrary shell regardless of authorized_keys restrictions, so this must
# never point at an admin account.
RESTRICTED_USER=${RESTRICTED_USER:-Jarvis}
LEGACY_USER=Nick
SSHD_DROPIN_SRC="$HERE/sshd-jarvis.conf"
SSHD_DROPIN_DEST=/etc/ssh/sshd_config.d/jarvis-nas.conf
DESIRED_SHELL=/bin/bash

[[ "$RESTRICTED_USER" != root ]] || { echo 'Refusing: RESTRICTED_USER must not be root.'; exit 1; }
NAS_HOME=$(getent passwd "$RESTRICTED_USER" | cut -d: -f6 || true)
[[ -n "$NAS_HOME" && -d "$NAS_HOME" ]] || { echo "No such user or missing home directory: $RESTRICTED_USER"; exit 1; }
if id -nG "$RESTRICTED_USER" | tr ' ' '\n' | grep -qxE 'admin|administrators|root'; then
    echo "Refusing: $RESTRICTED_USER is in an admin/root group. UGOS's global ForceCommand (/etc/ssh/force_command.sh) gives such accounts an unrestricted shell no matter what authorized_keys says." >&2
    exit 1
fi
RESTRICTED_GROUP=$(id -gn "$RESTRICTED_USER")
CURRENT_SHELL=$(getent passwd "$RESTRICTED_USER" | cut -d: -f7)

case ${1:---preview} in --preview|--apply) ;; *) exit 64 ;; esac
for key in jarvis_nas_recovery.pub jarvis_nas_status.pub; do
    [[ $(wc -l < "$HERE/$key") == 1 ]] && ssh-keygen -lf "$HERE/$key"
    [[ $(cut -d' ' -f1 "$HERE/$key") == ssh-ed25519 ]] || exit 1
done
AUTH_LINE='command="/volume1/docker/watchdog/recover_stack.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc '
STATUS_LINE='command="/volume1/docker/watchdog/stack_status.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc '
RULE="$RESTRICTED_USER ALL=(root) NOPASSWD: /usr/local/libexec/jarvis-nas-launch *"
printf 'Proposed authorized_keys additions (%s):\n%s%s\n%s%s\nSudo rule:\n%s\n' \
    "$NAS_HOME/.ssh/authorized_keys" "$AUTH_LINE" "$(cat "$HERE/jarvis_nas_recovery.pub")" "$STATUS_LINE" "$(cat "$HERE/jarvis_nas_status.pub")" "$RULE"
if [[ "$CURRENT_SHELL" == "$DESIRED_SHELL" ]]; then
    echo "Shell: $RESTRICTED_USER already uses $DESIRED_SHELL."
else
    echo "Shell: $RESTRICTED_USER is $CURRENT_SHELL; would change to $DESIRED_SHELL (forced commands are invoked via the account's shell, and nologin/false ignore them entirely; every key on this account is command=-restricted, so a working shell does not grant interactive access)."
fi
for file in recover_stack.sh stack_status.sh jarvis-nas-launch; do
    DEST="$WD/$file"
    [[ $file != jarvis-nas-launch ]] || DEST=/usr/local/libexec/jarvis-nas-launch
    bash -n "$HERE/$file"
    diff -u --label "$DEST (current)" --label "$DEST (proposed)" "$(test -f "$DEST" && echo "$DEST" || echo /dev/null)" "$HERE/$file" || [[ $? == 1 ]]
done
diff -u --label "$SSHD_DROPIN_DEST (current)" --label "$SSHD_DROPIN_DEST (proposed)" \
    "$(test -f "$SSHD_DROPIN_DEST" && echo "$SSHD_DROPIN_DEST" || echo /dev/null)" "$SSHD_DROPIN_SRC" || [[ $? == 1 ]]
if [[ "$RESTRICTED_USER" != "$LEGACY_USER" ]]; then
    LEGACY_HOME=$(getent passwd "$LEGACY_USER" | cut -d: -f6 || true)
    LEGACY_AUTH="${LEGACY_HOME:-}/.ssh/authorized_keys"
    if [[ -n "$LEGACY_HOME" && -f "$LEGACY_AUTH" ]]; then
        REMOVE_COUNT=0
        for key in recovery status; do
            BLOB=$(cut -d' ' -f2 "$HERE/jarvis_nas_$key.pub")
            grep -Fq "$BLOB" "$LEGACY_AUTH" && REMOVE_COUNT=$((REMOVE_COUNT + 1))
        done
        if [[ $REMOVE_COUNT -gt 0 ]]; then
            echo "Would remove $REMOVE_COUNT restricted key line(s) from $LEGACY_AUTH (that account keeps its own unrestricted key untouched)."
        else
            echo "$LEGACY_AUTH already has no restricted-key lines to remove."
        fi
    fi
fi
cat <<PLAN
Permission changes (non-recursive): remove extended/default ACLs; root:root
755 on /volume1/docker and watchdog, root:root 644 on compose, root:root 600
on qbit_creds.env; root owns watchdog scripts/lock/state/logs (scripts 755,
status/logs/counters 644, lock 600). $RESTRICTED_USER home 755; .ssh 700;
authorized_keys 600; remove their extended/default ACLs. Existing authorized
keys for $RESTRICTED_USER are preserved; the two restricted keys are removed
from $LEGACY_USER's authorized_keys once installed here. A Match block in
$SSHD_DROPIN_DEST disables UGOS's global ForceCommand for $RESTRICTED_USER
only, restored to the current state everywhere else. sshd -t must pass
before anything else is touched, and it is never reloaded automatically.
Backups include scripts, authorized_keys, sudo rule, sshd config, the prior
shell, credentials, compose, and getfacl output for restoration. No
containers or cron schedules changed.
PLAN
[[ ${1:---preview} == --apply ]] || exit 0
[[ $EUID == 0 ]] || { echo 'Run --apply with sudo.'; exit 77; }
for tool in getfacl setfacl visudo nohup setsid flock sshd usermod; do command -v "$tool" >/dev/null; done
exec 9>"$WD/stack.lock"
flock -n 9 || { echo 'Stack operation in progress; retry later.'; exit 75; }
BACKUP=/var/backups/jarvis-nas/$(date -u +%Y%m%dT%H%M%SZ)-ssh
umask 077
mkdir -p "$BACKUP" /var/lib/jarvis-nas/requests /usr/local/libexec "$NAS_HOME/.ssh"

# Highest blast radius first: validate the sshd change in isolation and
# revert immediately on any failure, before touching permissions or keys.
[[ ! -e "$SSHD_DROPIN_DEST" ]] || cp -a "$SSHD_DROPIN_DEST" "$BACKUP/jarvis-nas.conf.before"
install -o root -g root -m 644 "$SSHD_DROPIN_SRC" "$SSHD_DROPIN_DEST"
if ! sshd -t; then
    echo "sshd -t FAILED with the $RESTRICTED_USER Match block installed; reverting it. Nothing else was changed." >&2
    if [[ -e "$BACKUP/jarvis-nas.conf.before" ]]; then
        mv -f "$BACKUP/jarvis-nas.conf.before" "$SSHD_DROPIN_DEST"
    else
        rm -f "$SSHD_DROPIN_DEST"
    fi
    sshd -t || echo 'WARNING: sshd -t still fails after reverting; inspect /etc/ssh/sshd_config by hand before reloading anything.' >&2
    exit 1
fi
echo "sshd -t passed with $SSHD_DROPIN_DEST installed. Nothing is live until you run: systemctl reload ssh"

if [[ "$CURRENT_SHELL" != "$DESIRED_SHELL" ]]; then
    printf '%s\n' "$CURRENT_SHELL" > "$BACKUP/shell.before"
    usermod -s "$DESIRED_SHELL" "$RESTRICTED_USER"
    echo "Changed $RESTRICTED_USER's shell from $CURRENT_SHELL to $DESIRED_SHELL."
fi

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
    chown "$RESTRICTED_USER:$RESTRICTED_GROUP" "$path"
done
chmod 755 "$NAS_HOME"
chmod 700 "$NAS_HOME/.ssh"
chmod 600 "$NAS_HOME/.ssh/authorized_keys"
install -o root -g root -m 440 "$BACKUP/sudoers.proposed" /etc/sudoers.d/jarvis-nas
visudo -cf /etc/sudoers.d/jarvis-nas

# Migration cleanup: the two restricted keys previously lived on $LEGACY_USER
# (an admin-group account), which UGOS's global ForceCommand always gives an
# unrestricted shell regardless of these command= restrictions. Remove them
# there now that $RESTRICTED_USER holds the restricted copies, leaving every
# other line in $LEGACY_USER's authorized_keys (including their normal login
# key) untouched.
if [[ "$RESTRICTED_USER" != "$LEGACY_USER" ]]; then
    LEGACY_HOME=$(getent passwd "$LEGACY_USER" | cut -d: -f6 || true)
    LEGACY_AUTH="${LEGACY_HOME:-}/.ssh/authorized_keys"
    if [[ -n "$LEGACY_HOME" && -f "$LEGACY_AUTH" ]]; then
        mkdir -p "$BACKUP$(dirname "$LEGACY_AUTH")"
        cp -a "$LEGACY_AUTH" "$BACKUP$LEGACY_AUTH.before-cleanup"
        TMP_AUTH=$(mktemp)
        cp "$LEGACY_AUTH" "$TMP_AUTH"
        REMOVED=0
        for key in recovery status; do
            BLOB=$(cut -d' ' -f2 "$HERE/jarvis_nas_$key.pub")
            if grep -Fq "$BLOB" "$TMP_AUTH"; then
                grep -Fv "$BLOB" "$TMP_AUTH" > "$TMP_AUTH.next"
                mv "$TMP_AUTH.next" "$TMP_AUTH"
                REMOVED=$((REMOVED + 1))
            fi
        done
        if [[ $REMOVED -gt 0 ]]; then
            cat "$TMP_AUTH" > "$LEGACY_AUTH"
            echo "Removed $REMOVED restricted key line(s) from $LEGACY_AUTH (backup: $BACKUP$LEGACY_AUTH.before-cleanup)."
        else
            echo "$LEGACY_AUTH already had no restricted-key lines."
        fi
        rm -f "$TMP_AUTH"
    fi
fi

# Confirm the read access stack_status.sh needs, and that qbit_creds.env
# stays out of reach, without granting anything through the shared-folder
# permission GUI.
NEEDS_ACL=0
sudo -u "$RESTRICTED_USER" test -x "$WD" || NEEDS_ACL=1
for f in "$WD/recovery-status.json" "$WD/.last_action" "$WD/.rotate_cooldown" "$WD/watchdog.log" "$WD/indexer_watchdog.log"; do
    [[ -e "$f" ]] || continue
    sudo -u "$RESTRICTED_USER" test -r "$f" || NEEDS_ACL=1
done
if [[ $NEEDS_ACL == 1 ]]; then
    setfacl -m "u:$RESTRICTED_USER:r-x" "$WD"
    for f in "$WD/recovery-status.json" "$WD/.last_action" "$WD/.rotate_cooldown" "$WD/watchdog.log" "$WD/indexer_watchdog.log"; do
        [[ -e "$f" ]] || continue
        setfacl -m "u:$RESTRICTED_USER:r--" "$f"
    done
    echo "Granted $RESTRICTED_USER an explicit read ACL on watchdog status/log paths; plain permissions were not enough."
else
    echo "$RESTRICTED_USER can already read watchdog status/log paths; no extra grant needed."
fi
if [[ -e "$WD/qbit_creds.env" ]] && sudo -u "$RESTRICTED_USER" test -r "$WD/qbit_creds.env"; then
    echo "WARNING: $RESTRICTED_USER can read qbit_creds.env; investigate before enabling recovery." >&2
fi

printf 'Installed. Nothing was reloaded or restarted. Root-only backup: %s\nsshd config is only live once you run: systemctl reload ssh\n' "$BACKUP"
