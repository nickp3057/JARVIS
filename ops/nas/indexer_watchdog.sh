#!/bin/bash
# indexer_watchdog.sh v4 - detect burned Mullvad exit IPs, rotate node.
WD=/volume1/docker/watchdog
. "$WD/qbit_creds.env"
LOG="$WD/indexer_watchdog.log"
CSV="$WD/indexer_stats.csv"
STATE="$WD/.byparr_slow"
COOLDOWN="$WD/.rotate_cooldown"
TARGET="https://1337x.to/search/1080p/1/"
SLOW=50          # seconds; above this the IP is going bad
STRIKES=2        # consecutive slow/failed probes before rotating
COOL=3600        # min seconds between rotations
TS=$(date '+%Y-%m-%d %H:%M:%S')

log(){ echo "[$TS] $*" >> "$LOG"; }
exec 9>"$WD/stack.lock"; flock -n 9 || exit 0

[ -f "$CSV" ] || echo "timestamp|exit_ip|exit_host|solve_secs|result|strikes|action" > "$CSV"

MV=$(docker exec gluetun wget -qO- --timeout=10 https://am.i.mullvad.net/json 2>/dev/null)
EIP=$(echo "$MV" | python3 -c "import sys,json;print(json.load(sys.stdin).get('ip','?'))" 2>/dev/null)
EHOST=$(echo "$MV" | python3 -c "import sys,json;print(json.load(sys.stdin).get('mullvad_exit_ip_hostname','?'))" 2>/dev/null)

T0=$(date +%s.%N)
BODY=$(docker exec gluetun sh -c "wget -qO- --timeout=200 \
  --post-data='{\"cmd\":\"request.get\",\"url\":\"$TARGET\",\"maxTimeout\":180000}' \
  --header='Content-Type: application/json' http://localhost:8191/v1" 2>/dev/null)
T1=$(date +%s.%N)
SECS=$(echo "$T1 $T0" | awk '{printf "%.1f",$1-$2}')

case "$BODY" in *'"status":"ok"'*) RESULT=ok ;; *) RESULT=fail ;; esac
BAD=$(echo "$SECS" | awk -v s=$SLOW '{print ($1>s)?1:0}')
[ "$RESULT" = "fail" ] && BAD=1

N=$(cat "$STATE" 2>/dev/null || echo 0)
if [ "$BAD" = "1" ]; then N=$((N+1)); else N=0; fi
echo "$N" > "$STATE"

ACTION="-"
if [ "$N" -ge "$STRIKES" ]; then
  LAST=$(cat "$COOLDOWN" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  if [ $((NOW-LAST)) -lt "$COOL" ]; then
    ACTION="cooldown"
  else
    ACTION="rotate"
    log "rotating: $EHOST bad x$N (last ${SECS}s)"
    # Failed attempts must also cool down; otherwise every cron run restarts the stack.
    echo "$NOW" > "$COOLDOWN"
    if ! "$WD/recover_stack.sh" indexer; then
      log "ERROR: shared recovery failed; strikes and cooldown retained"
      exit 1
    fi
    NEW=$(docker exec gluetun wget -qO- --timeout=10 https://am.i.mullvad.net/json 2>/dev/null \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('mullvad_exit_ip_hostname','?'))" 2>/dev/null)
    log "rotated $EHOST -> $NEW"
    echo 0 > "$STATE"
  fi
fi

echo "$TS|$EIP|$EHOST|$SECS|$RESULT|$N|$ACTION" >> "$CSV"
log "$RESULT ${SECS}s node=$EHOST strikes=$N action=$ACTION"
