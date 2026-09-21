#!/bin/bash
# Run on NAS as root. Preview is read-only; --apply repeats the diff first.
set -euo pipefail
WD=/volume1/docker/watchdog
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
case ${1:---preview} in --preview|--apply) ;; *) exit 64 ;; esac
for file in recover_stack.sh dht_watchdog.sh indexer_watchdog.sh; do
    bash -n "$HERE/$file"
    diff -u --label "$WD/$file (current)" --label "$WD/$file (proposed)" "$(test -f "$WD/$file" && echo "$WD/$file" || echo /dev/null)" "$HERE/$file" || [[ $? == 1 ]]
done
[[ ${1:---preview} == --apply ]] || exit 0
[[ $EUID == 0 ]] || { echo 'Run --apply with sudo.'; exit 77; }
exec 9>"$WD/stack.lock"
flock -n 9 || { echo 'Stack operation in progress; retry later.'; exit 75; }
# Fail closed if the live trigger logic doesn't match a hash we've reviewed.
# Two hashes are accepted per file: the original pre-refactor script found
# live on the NAS on 2026-09-20, and the refactored version this same
# installer already writes (calls the shared recover_stack.sh instead of
# duplicating the docker chain inline) — so a re-run after an earlier
# --apply, e.g. to pick up a recover_stack.sh-only change, isn't mistaken
# for unreviewed drift. Anything else stops here instead of being silently
# overwritten.
check_known_hash() {
    local file=$1 live
    shift
    [[ -e "$WD/$file" ]] || { echo "Missing expected live file: $WD/$file" >&2; return 1; }
    live=$(sha256sum "$WD/$file" | cut -d' ' -f1)
    for known in "$@"; do
        [[ "$live" != "$known" ]] || return 0
    done
    echo "Unreviewed change in $WD/$file (sha256 $live matches neither known-good hash); stop and review before installing." >&2
    return 1
}
check_known_hash dht_watchdog.sh \
    832e2678f6cf1d4368507faa23691b18eae6ab649c47e49ef43f64302a72dda3 \
    84cab6a3ce811b70209e7690445fb6c03dfd713913ba683f6b98a522e9490033
check_known_hash indexer_watchdog.sh \
    30d7e41cbcbe14654a367d0a1d7deef783f6eae1ed02cb77464e8dc844a4b7a7 \
    9522647964651944450f80ced31fb238a23a9043a361da8a4a31903a2a7b42fd
BACKUP="$WD/backups/$(date -u +%Y%m%dT%H%M%SZ)-recovery"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
for file in recover_stack.sh dht_watchdog.sh indexer_watchdog.sh; do
    [[ ! -e "$WD/$file" ]] || cp -a "$WD/$file" "$BACKUP/$file"
done
for file in recover_stack.sh dht_watchdog.sh indexer_watchdog.sh; do
    # sed is available on UGOS; preserve the reviewed text exactly.
    sed -n '1,$p' "$HERE/$file" > "$WD/$file.new"
    chmod 755 "$WD/$file.new"
    chown root:root "$WD/$file.new"
    mv -f "$WD/$file.new" "$WD/$file"
done
printf 'Installed without restarting anything. Backups: %s\n' "$BACKUP"
