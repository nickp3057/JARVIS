#!/bin/bash
#
# dht_watchdog.sh
# Detects the Gluetun -> qBittorrent zombie DHT namespace issue and
# auto-remediates by force-recreating gluetun + dependent containers.
#
# Install: root crontab only, add:
#   */5 * * * * /volume1/docker/watchdog/dht_watchdog.sh >> /volume1/docker/watchdog/watchdog.log 2>&1
#
# Requires: /volume1/docker/watchdog/qbit_creds.env with:
#   QBIT_USER=your_username
#   QBIT_PASS=your_password

set -uo pipefail

STACK_DIR="/volume1/docker"
WATCHDOG_DIR="/volume1/docker/watchdog"
CREDS_FILE="$WATCHDOG_DIR/qbit_creds.env"
QBIT_URL="http://localhost:8080"
LOG_FILE="$WATCHDOG_DIR/watchdog.log"
STATE_FILE="$WATCHDOG_DIR/.zero_count"
CONSECUTIVE_THRESHOLD=3   # require 3 consecutive 5-min checks at 0 nodes (~15 min) before acting
COOLDOWN_FILE="$WATCHDOG_DIR/.last_action"
COOLDOWN_SECONDS=1800     # don't re-trigger remediation more than once per 30 min
# The cooldown starts at every remediation *attempt*. It used to be written
# only after a success, so a failed run left the zero counter at threshold and
# the very next check restarted everything again (2026-09-22 05:40 failed
# verification, 05:50 re-ran immediately); a persistent failure would loop a
# full recreate every ~13 min. One quick retry after a failed attempt is still
# allowed -- the 05:50 retry is what actually fixed it -- so at most two
# attempts happen per cooldown window.
FAILED_FILE="$WATCHDOG_DIR/.last_action_failed"
RETRY_FILE="$WATCHDOG_DIR/.retry_used"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

log() { echo "[$(timestamp)] $1"; }

mkdir -p "$WATCHDOG_DIR"
exec 9>"$WATCHDOG_DIR/stack.lock"
flock -n 9 || exit 0

if [[ ! -f "$CREDS_FILE" ]]; then
    log "ERROR: creds file not found at $CREDS_FILE — aborting check"
    exit 1
fi
# shellcheck disable=SC1090
source "$CREDS_FILE"

if [[ -z "${QBIT_USER:-}" || -z "${QBIT_PASS:-}" ]]; then
    log "ERROR: QBIT_USER or QBIT_PASS not set in $CREDS_FILE"
    exit 1
fi

COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

# --- Authenticate ---
# qBittorrent's CSRF protection requires Referer/Origin headers matching QBIT_URL,
# even for localhost requests, or it 403s without ever reaching the login handler.
# Newer qBittorrent returns HTTP 200/204 with an empty body on success (auth cookie
# is what actually matters), rather than the older "Ok." text body — so we check the
# HTTP status code and cookie presence instead of body content.
LOGIN_HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -c "$COOKIE_JAR" \
    -H "Referer: ${QBIT_URL}" \
    -H "Origin: ${QBIT_URL}" \
    --data "username=${QBIT_USER}&password=${QBIT_PASS}" \
    "${QBIT_URL}/api/v2/auth/login")

if [[ "$LOGIN_HTTP_CODE" != "200" && "$LOGIN_HTTP_CODE" != "204" ]] || ! grep -q "QBT_SID" "$COOKIE_JAR" 2>/dev/null; then
    log "ERROR: qBittorrent login failed (HTTP $LOGIN_HTTP_CODE, no session cookie) — check credentials or qbittorrent status"
    exit 1
fi

# --- Get DHT node count from transfer info ---
# qBittorrent doesn't expose DHT nodes directly via a clean single field pre-v5 API,
# so we pull it from sync/maindata's server_state.dht_nodes
MAINDATA=$(curl -s -b "$COOKIE_JAR" \
    -H "Referer: ${QBIT_URL}" \
    -H "Origin: ${QBIT_URL}" \
    "${QBIT_URL}/api/v2/sync/maindata")
DHT_NODES=$(echo "$MAINDATA" | grep -o '"dht_nodes":[0-9]*' | grep -o '[0-9]*$')

if [[ -z "$DHT_NODES" ]]; then
    log "WARN: could not parse dht_nodes from maindata response — skipping this check"
    exit 0
fi

log "DHT nodes: $DHT_NODES"

if [[ "$DHT_NODES" -gt 0 ]]; then
    # Healthy — reset consecutive-zero counter
    rm -f "$STATE_FILE"
    exit 0
fi

# --- DHT is at zero: track consecutive checks ---
COUNT=0
if [[ -f "$STATE_FILE" ]]; then
    COUNT=$(cat "$STATE_FILE")
fi
COUNT=$((COUNT + 1))
echo "$COUNT" > "$STATE_FILE"

log "DHT nodes at zero (consecutive check $COUNT/$CONSECUTIVE_THRESHOLD)"

if [[ "$COUNT" -lt "$CONSECUTIVE_THRESHOLD" ]]; then
    log "Not yet at threshold — waiting for further confirmation before acting"
    exit 0
fi

# --- Cooldown check to avoid restart-looping ---
NOW=$(date +%s)
IN_COOLDOWN=0
if [[ -f "$COOLDOWN_FILE" ]]; then
    LAST_ACTION=$(cat "$COOLDOWN_FILE")
    ELAPSED=$((NOW - LAST_ACTION))
    [[ "$ELAPSED" -lt "$COOLDOWN_SECONDS" ]] && IN_COOLDOWN=1
fi
if [[ "$IN_COOLDOWN" == 1 ]]; then
    if [[ -f "$FAILED_FILE" && ! -f "$RETRY_FILE" ]]; then
        log "Previous remediation failed ${ELAPSED}s ago — allowing one retry inside the cooldown window"
        touch "$RETRY_FILE"
    else
        log "Within cooldown window (${ELAPSED}s since last attempt, limit ${COOLDOWN_SECONDS}s) — skipping remediation, needs manual look"
        exit 0
    fi
else
    # Fresh window: the one-retry allowance resets.
    rm -f "$RETRY_FILE"
fi

log "=== Threshold reached: zero DHT nodes confirmed across $CONSECUTIVE_THRESHOLD checks ==="
log "Capturing diagnostics before remediation..."

{
    echo "--- gluetun mullvad status ---"
    sudo docker exec gluetun wget -qO- https://am.i.mullvad.net/json 2>&1
    echo ""
    echo "--- gluetun recent logs (last 100 lines) ---"
    sudo docker logs gluetun --tail 100 2>&1
    echo ""
    echo "--- docker ps ---"
    sudo docker ps --format 'table {{.Names}}\t{{.Status}}' 2>&1
} >> "$LOG_FILE"

log "Running force-recreate chain..."
# Start the cooldown before the attempt, whatever its outcome.
echo "$NOW" > "$COOLDOWN_FILE"
if ! "$WATCHDOG_DIR/recover_stack.sh" dht; then
    touch "$FAILED_FILE"
    log "ERROR: shared recovery failed; counters retained, cooldown started"
    exit 1
fi

log "Remediation complete. Resetting counters."
rm -f "$STATE_FILE" "$FAILED_FILE" "$RETRY_FILE"

log "=== Watchdog action finished ==="
