#!/bin/bash
# Root cron guard: keep the restricted Jarvis account's login shell usable.
#
# sshd runs even a command="..." forced command through the account's login
# shell, so with /usr/sbin/nologin every restricted key fails with "This
# account is currently not available." UGOS rewrites /etc/passwd with nologin
# for non-admin users whenever an account is saved in its UI (observed
# 2026-09-20 22:54, 1h45m after install-restricted-ssh.sh set /bin/bash),
# silently disabling both the status and recovery keys.
#
# This restores exactly the state install-restricted-ssh.sh establishes and
# nothing more: no key, restriction, sudo rule, or sshd setting is touched.
# It refuses to act if the account is ever in an admin/root group, because
# UGOS's global ForceCommand would then give it a real shell.
#
# Install (root crontab):  */5 * * * * /volume1/docker/watchdog/ensure_jarvis_shell.sh
set -uo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
USER_NAME=Jarvis
DESIRED=/bin/bash
LOG=/volume1/docker/watchdog/watchdog.log
[[ $EUID -eq 0 ]] || exit 77
ENTRY=$(getent passwd "$USER_NAME") || exit 0
CURRENT=${ENTRY##*:}
[[ "$CURRENT" == "$DESIRED" ]] && exit 0
if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qxE 'admin|administrators|root'; then
    printf '[%s] ensure_jarvis_shell: REFUSING, %s is in an admin/root group; shell left as %s\n' "$(date -Iseconds)" "$USER_NAME" "$CURRENT" >> "$LOG"
    exit 1
fi
usermod -s "$DESIRED" "$USER_NAME" &&
    printf '[%s] ensure_jarvis_shell: restored %s shell %s -> %s (UGOS account save resets it)\n' "$(date -Iseconds)" "$USER_NAME" "$CURRENT" "$DESIRED" >> "$LOG"
