#!/bin/bash
# Single recovery chain for root cron and the restricted Jarvis launcher.
set -uo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
WD=/volume1/docker/watchdog
COMPOSE=/volume1/docker/docker-compose.yaml
# The forced SSH command runs as Nick. Ignore/reject arbitrary client commands;
# only an explicit UUID request reaches the fixed, root-owned launcher.
if [[ $EUID -ne 0 ]]; then
    [[ $# == 0 ]] || exit 64
    if [[ ${SSH_ORIGINAL_COMMAND:-} == probe ]]; then
        echo 'jarvis-recovery-key: restricted; no restart performed'
        exit 0
    fi
    if [[ ${SSH_ORIGINAL_COMMAND:-} =~ ^recover\ ([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$ ]]; then
        exec /usr/bin/sudo -n /usr/local/libexec/jarvis-nas-launch "${BASH_REMATCH[1]}"
    fi
    if [[ ${SSH_ORIGINAL_COMMAND:-} =~ ^update\ ([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$ ]]; then
        exec /usr/bin/sudo -n /usr/local/libexec/jarvis-nas-launch update "${BASH_REMATCH[1]}"
    fi
    echo 'DENIED: only probe, recover UUID or update UUID is allowed.' >&2
    exit 64
fi
SOURCE=${1:-manual}
REQUEST=${2:-$(cat /proc/sys/kernel/random/uuid)}
[[ $# -le 2 && "$SOURCE" =~ ^(manual|dht|indexer|jarvis)$ && "$REQUEST" =~ ^[a-f0-9-]{36}$ ]] || exit 64
[[ $EUID -eq 0 ]] || { echo 'ERROR: recovery requires the restricted sudo launcher or root cron.'; exit 77; }
umask 077
exec >> "$WD/watchdog.log" 2>&1
log() { printf '[%s] recovery source=%s request=%s %s\n' "$(date -Iseconds)" "$SOURCE" "$REQUEST" "$*"; }

# An indexer check already holds FD 9. Reuse that exact open file description;
# opening the same path again and flocking it would deadlock against ourselves.
if [[ ! -e /proc/$$/fd/9 ]] || [[ $(stat -Lc '%d:%i' /proc/$$/fd/9) != $(stat -Lc '%d:%i' "$WD/stack.lock") ]]; then
    exec 9>"$WD/stack.lock" || exit 1
fi
flock -n 9 || { log 'BUSY: another stack operation holds stack.lock; no restart performed'; exit 75; }

COOKIE=''
PHASE=starting
DHT=-1
HEALTH=unknown
STARTED=$(date +%s)
write_status() {
    local state=$1 code=$2 tmp
    tmp=$(mktemp "$WD/.recovery-status.XXXXXX") || return 1
    printf '{"request_id":"%s","source":"%s","state":"%s","phase":"%s","started_at":%s,"updated_at":%s,"exit_code":%s,"dht_nodes":%s,"gluetun_health":"%s"}\n' \
        "$REQUEST" "$SOURCE" "$state" "$PHASE" "$STARTED" "$(date +%s)" "$code" "$DHT" "$HEALTH" > "$tmp" &&
        chmod 644 "$tmp" && mv -f "$tmp" "$WD/recovery-status.json"
}
finish() {
    local rc=$?
    trap - EXIT
    [[ -z "$COOKIE" ]] || rm -f "$COOKIE"
    if [[ $rc -eq 0 ]]; then
        PHASE=complete
        write_status success 0 || rc=1
        log "SUCCESS: gluetun=$HEALTH dht_nodes=$DHT"
    else
        write_status failed "$rc" || true
        log "FAILED: phase=$PHASE exit=$rc; chain aborted"
    fi
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
step() {
    PHASE=$1; shift
    write_status running 0 || exit 1
    log "STEP: $PHASE"
    "$@"
    local rc=$?
    [[ $rc -eq 0 ]] || { log "ERROR: $PHASE exit=$rc"; exit "$rc"; }
}
write_status running 0 || exit 1
log 'START: full arr recovery; active downloads will disconnect'
# Explicit service allowlist and --no-deps prevent unrelated compose services
# (especially mooney-plaid-backend) from being created or restarted. The five
# dependents are also explicitly rm -f'd before recreation: a dead container
# left behind by a prior failed/aborted recovery keeps its name, and Compose
# then refuses to recreate it ("Conflict. The container name ... is already
# in use") until it's gone. Confirmed live 2026-09-20 during a manual recovery.
step remove-gluetun docker rm -f gluetun
step recreate-gluetun docker compose --project-name stack --file "$COMPOSE" up -d --no-deps --force-recreate gluetun
step vpn-settle sleep 20
step remove-dependents docker rm -f qbittorrent radarr sonarr prowlarr flaresolverr
step recreate-dependents docker compose --project-name stack --file "$COMPOSE" up -d --no-deps --force-recreate qbittorrent radarr sonarr prowlarr flaresolverr
step byparr-cold-start sleep 130

PHASE=verify
write_status running 0 || exit 1
[[ -r "$WD/qbit_creds.env" ]] || { log 'ERROR: missing qbit_creds.env'; exit 1; }
# Root-owned credentials, installed alongside the root-owned script.
source "$WD/qbit_creds.env"
[[ -n ${QBIT_USER:-} && -n ${QBIT_PASS:-} ]] || { log 'ERROR: missing qBit credentials'; exit 1; }
COOKIE=$(mktemp) || exit 1
QBIT=http://localhost:8080
DEADLINE=$(( $(date +%s) + 300 ))
while (( $(date +%s) < DEADLINE )); do
    HEALTH=$(docker inspect --format '{{.State.Health.Status}}' gluetun 2>/dev/null) || HEALTH=unknown
    case "$HEALTH" in healthy|unhealthy|starting) ;; *) HEALTH=unknown ;; esac
    # Reauthenticate on every poll: recreation invalidates the old cookie.
    curl -fsS --connect-timeout 3 --max-time 5 -o /dev/null -c "$COOKIE" \
        -H "Referer: $QBIT" -H "Origin: $QBIT" \
        --data-urlencode "username=$QBIT_USER" --data-urlencode "password=$QBIT_PASS" \
        "$QBIT/api/v2/auth/login" 2>/dev/null || true
    INFO=$(curl -fsS --connect-timeout 3 --max-time 5 -b "$COOKIE" -H "Referer: $QBIT" \
        "$QBIT/api/v2/transfer/info" 2>/dev/null) || INFO='{}'
    DHT=$(printf '%s' "$INFO" | jq -er '.dht_nodes | select(type == "number" and . >= 0 and . == floor)' 2>/dev/null) || DHT=-1
    if [[ $DHT == -1 ]]; then
        # Some qBit versions expose DHT only through sync/maindata.
        INFO=$(curl -fsS --connect-timeout 3 --max-time 5 -b "$COOKIE" -H "Referer: $QBIT" \
            "$QBIT/api/v2/sync/maindata" 2>/dev/null) || INFO='{}'
        DHT=$(printf '%s' "$INFO" | jq -er '.server_state.dht_nodes | select(type == "number" and . >= 0 and . == floor)' 2>/dev/null) || DHT=-1
    fi
    log "VERIFY: gluetun=$HEALTH dht_nodes=$DHT"
    [[ $HEALTH == healthy && $DHT -gt 0 ]] && exit 0
    write_status running 0 || exit 1
    sleep 10 || exit $?
done
log 'ERROR: verification timed out after approximately 5 minutes'
exit 1
