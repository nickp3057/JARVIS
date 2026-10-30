#!/bin/bash
# stack_updater.sh - weekly image updates (root cron) and on demand (Jarvis,
# via jarvis-nas-launch update). CLI only (GUI breaks gluetun namespace).
# Root runs this: it must stay root-owned 755 with no ACLs (installer enforces).
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
WD=/volume1/docker/watchdog
LOG="$WD/updater.log"
STATUS="$WD/updater_status.txt"
AUTO="gluetun qbittorrent radarr sonarr prowlarr flaresolverr tailscale letterboxd-plex-sync"
CHECK_ONLY=0
[ "$1" = "--check" ] && CHECK_ONLY=1

TS(){ date '+%Y-%m-%d %H:%M:%S'; }
log(){ echo "[$(TS)] $*" >> "$LOG"; }

# running image ID vs ID the tag now points to
stale(){
  local c="$1" run tag
  run=$(docker inspect --format '{{.Image}}' "$c" 2>/dev/null) || return 1
  tag=$(docker image inspect --format '{{.Id}}' \
        "$(docker inspect --format '{{.Config.Image}}' "$c" 2>/dev/null)" 2>/dev/null) || return 1
  [ -n "$run" ] && [ -n "$tag" ] && [ "$run" != "$tag" ]
}

exec 9>"$WD/stack.lock"
flock -w 900 9 || { log "lock timeout, aborting"; exit 1; }

log "=== updater start (check_only=$CHECK_ONLY) ==="
: > "$STATUS"
echo "Last run: $(TS)" >> "$STATUS"

cd /volume1/docker || exit 1
CHANGED=""
for S in $AUTO; do
  docker compose --project-name stack pull "$S" >> "$LOG" 2>&1
  if stale "$S"; then
    CHANGED="$CHANGED $S"
    echo "UPDATE: $S" >> "$STATUS"
  fi
done

if [ -n "$CHANGED" ] && [ "$CHECK_ONLY" = "0" ]; then
  if echo "$CHANGED" | grep -qw gluetun; then
    log "gluetun changed -> full chain recreate"
    docker rm -f gluetun >> "$LOG" 2>&1
    docker compose --project-name stack up -d --force-recreate gluetun >> "$LOG" 2>&1
    sleep 20
    docker compose --project-name stack up -d --force-recreate \
      qbittorrent radarr sonarr prowlarr flaresolverr >> "$LOG" 2>&1
    sleep 130
    OTHER=$(echo "$CHANGED" | tr ' ' '\n' | grep -vwE 'gluetun|qbittorrent|radarr|sonarr|prowlarr|flaresolverr' | tr '\n' ' ')
    [ -n "$OTHER" ] && docker compose --project-name stack up -d $OTHER >> "$LOG" 2>&1
  else
    docker compose --project-name stack up -d $CHANGED >> "$LOG" 2>&1
    echo "$CHANGED" | grep -qw flaresolverr && sleep 130
  fi
  echo "APPLIED:$CHANGED" >> "$STATUS"
  log "applied:$CHANGED"
elif [ -z "$CHANGED" ]; then
  echo "stack: up to date" >> "$STATUS"
  log "stack: no updates"
fi

cd /volume1/docker/plex || true
docker compose --project-name plex pull >> "$LOG" 2>&1
if stale plex; then
  if [ "$CHECK_ONLY" = "0" ]; then
    docker compose --project-name plex up -d >> "$LOG" 2>&1
    echo "APPLIED: plex" >> "$STATUS"; log "plex updated"
  else
    echo "UPDATE: plex" >> "$STATUS"
  fi
else
  echo "plex: up to date" >> "$STATUS"
fi

for S in homeassistant immich_server; do
  CUR=$(docker inspect --format '{{.Config.Image}}' "$S" 2>/dev/null)
  [ -n "$CUR" ] && echo "MANUAL: $S ($CUR)" >> "$STATUS"
done
echo "SKIPPED: xteve, backend" >> "$STATUS"

DOWN=$(docker ps -a --filter 'status=exited' --format '{{.Names}}' | tr '\n' ' ')
[ -n "$DOWN" ] && { echo "EXITED: $DOWN" >> "$STATUS"; log "WARNING exited: $DOWN"; }
docker image prune -f >> "$LOG" 2>&1
log "=== updater done ==="
