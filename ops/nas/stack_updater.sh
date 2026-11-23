#!/bin/bash
# stack_updater.sh - weekly image updates (root cron) and on demand (Jarvis,
# via jarvis-nas-launch update). CLI only (GUI breaks gluetun namespace).
# Root runs this: it must stay root-owned 755 with no ACLs (installer enforces).
set -Eeuo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
WD=/volume1/docker/watchdog
LOG="$WD/updater.log"
STATUS="$WD/updater_status.txt"
AUTO="gluetun qbittorrent radarr radarr4k sonarr prowlarr flaresolverr tailscale letterboxd-plex-sync"
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; fi

TS(){ date '+%Y-%m-%d %H:%M:%S'; }
log(){ echo "[$(TS)] $*" >> "$LOG"; }

PHASE=initializing
failed() {
  local rc=$1
  trap - ERR
  echo "FAILED: $PHASE (exit $rc)" >> "$STATUS"
  log "FAILED: $PHASE (exit $rc); update aborted"
  exit "$rc"
}
trap 'failed "$?"' ERR

# running image ID vs ID the tag now points to
stale(){
  local c="$1" run tag
  run=$(docker inspect --format '{{.Image}}' "$c" 2>/dev/null) || return 2
  tag=$(docker image inspect --format '{{.Id}}' \
        "$(docker inspect --format '{{.Config.Image}}' "$c" 2>/dev/null)" 2>/dev/null) || return 2
  [ -n "$run" ] && [ -n "$tag" ] && [ "$run" != "$tag" ]
}

# Registries rate-limit now and then (lscr.io's "toomanyrequests" aborted the
# whole run on 2026-11-15 and 11-22): retry each pull with backoff, and skip
# an image that still won't pull rather than updating nothing.
PULL_TRIES=4
PULL_FAILED=""
pull_retry(){
  local service="$1" project="$2" n
  for n in $(seq 1 "$PULL_TRIES"); do
    docker compose --project-name "$project" pull $service >> "$LOG" 2>&1 && return 0
    if [ "$n" -lt "$PULL_TRIES" ]; then
      log "pull ${service:-$project} failed (try $n), retrying"
      sleep $((n * 30))
    fi
  done
  PULL_FAILED="$PULL_FAILED ${service:-$project}"
  echo "PULL FAILED: ${service:-$project}" >> "$STATUS"
  log "pull ${service:-$project} failed $PULL_TRIES times; skipping it this run"
  return 1
}

exec 9>"$WD/stack.lock"
flock -w 900 9 || { log "lock timeout, aborting"; exit 1; }

log "=== updater start (check_only=$CHECK_ONLY) ==="
: > "$STATUS"
echo "Last run: $(TS)" >> "$STATUS"

PHASE=stack-directory
cd /volume1/docker
CHANGED=""
for S in $AUTO; do
  PHASE="pull-$S"
  pull_retry "$S" stack || continue
  PHASE="inspect-$S"
  if stale "$S"; then
    CHANGED="$CHANGED $S"
    echo "UPDATE: $S" >> "$STATUS"
  else
    rc=$?
    [ "$rc" = 1 ] || failed "$rc"
  fi
done
# Nothing pulled at all: the registry or network is down, not one image.
if [ "$(echo $PULL_FAILED)" = "$(echo $AUTO)" ]; then PHASE=pull-all; failed 1; fi

if [ -n "$CHANGED" ] && [ "$CHECK_ONLY" = "0" ]; then
  if echo "$CHANGED" | grep -qw gluetun; then
    log "gluetun changed -> full chain recreate"
    PHASE=recreate-gluetun
    docker rm -f gluetun >> "$LOG" 2>&1
    docker compose --project-name stack up -d --no-deps --force-recreate gluetun >> "$LOG" 2>&1
    sleep 20
    PHASE=recreate-dependents
    docker rm -f qbittorrent radarr radarr4k sonarr prowlarr flaresolverr >> "$LOG" 2>&1
    docker compose --project-name stack up -d --no-deps --force-recreate \
      qbittorrent radarr radarr4k sonarr prowlarr flaresolverr >> "$LOG" 2>&1
    sleep 130
    OTHER=$(echo "$CHANGED" | tr ' ' '\n' | awk 'NF && !/^(gluetun|qbittorrent|radarr|radarr4k|sonarr|prowlarr|flaresolverr)$/' | tr '\n' ' ')
    PHASE=recreate-other
    if [ -n "$OTHER" ]; then docker compose --project-name stack up -d --no-deps $OTHER >> "$LOG" 2>&1; fi
  else
    PHASE=recreate-changed
    docker compose --project-name stack up -d --no-deps $CHANGED >> "$LOG" 2>&1
    if echo "$CHANGED" | grep -qw flaresolverr; then sleep 130; fi
  fi
  echo "APPLIED:$CHANGED" >> "$STATUS"
  log "applied:$CHANGED"
elif [ -z "$CHANGED" ]; then
  echo "stack: up to date" >> "$STATUS"
  log "stack: no updates"
fi

PHASE=plex-directory
cd /volume1/docker/plex
PHASE=pull-plex
if ! pull_retry "" plex; then
  :
elif stale plex; then
  if [ "$CHECK_ONLY" = "0" ]; then
    PHASE=recreate-plex
    docker compose --project-name plex up -d >> "$LOG" 2>&1
    echo "APPLIED: plex" >> "$STATUS"; log "plex updated"
  else
    echo "UPDATE: plex" >> "$STATUS"
  fi
else
  rc=$?
  [ "$rc" = 1 ] || failed "$rc"
  echo "plex: up to date" >> "$STATUS"
fi

for S in homeassistant immich_server; do
  CUR=$(docker inspect --format '{{.Config.Image}}' "$S" 2>/dev/null) || CUR=""
  [ -n "$CUR" ] && echo "MANUAL: $S ($CUR)" >> "$STATUS"
done
echo "SKIPPED: xteve, backend" >> "$STATUS"

PHASE=final-status
DOWN=$(docker ps -a --filter 'status=exited' --format '{{.Names}}' | tr '\n' ' ')
[ -n "$DOWN" ] && { echo "EXITED: $DOWN" >> "$STATUS"; log "WARNING exited: $DOWN"; }
# Keep old images available for manual rollback after an update.
if [ -n "$PULL_FAILED" ]; then
  # Core (brain/arr_stack.auto_retry_update) re-runs the updater on PARTIAL.
  echo "PARTIAL: could not pull$PULL_FAILED; everything else updated" >> "$STATUS"
  log "=== updater done (partial: could not pull$PULL_FAILED) ==="
  exit 3
fi
echo "SUCCESS: update completed" >> "$STATUS"
log "=== updater done ==="
