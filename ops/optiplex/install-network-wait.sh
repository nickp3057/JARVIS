#!/bin/sh
# Make network-online.target actually wait for the Wi-Fi link on the Optiplex.
#
# Problem (confirmed 2026-09-22 after a power cut): jarvis-api.service already
# has After=/Wants=network-online.target, but on this ifupdown + dhcpcd box
# nothing holds that target back -- ifupdown-wait-online.service is disabled --
# so it is reached ~2 s into boot, before wlp2s0 is associated and leased.
# Every integration then fails its first call with "Network is unreachable".
#
# Fix: enable Debian's own ifupdown-wait-online.service and have it ping the
# IPv4 gateway. The "route" method is not used on purpose: it accepts an IPv6
# default route from a router advertisement, which can appear before the IPv4
# lease that HA/NAS (IPv4-only addresses) need. The default "ifup" method only
# checks that ifup ran for the hotplug interface, not that DHCP finished.
#
# Bounded: after WAIT_ONLINE_TIMEOUT the wait unit fails and jarvis-api starts
# anyway (Wants=, not Requires=), and the app's own startup grace absorbs it.
#
# Usage (on the Optiplex):  sudo sh ops/optiplex/install-network-wait.sh [gateway]
set -eu
GATEWAY=${1:-10.0.0.1}
TIMEOUT=90
CONF=/etc/default/networking

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
echo "$GATEWAY" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || { echo "Gateway must be an IPv4 address." >&2; exit 1; }

cp -p "$CONF" "$CONF.bak-$(date +%Y%m%d%H%M%S)"
set_var() {
    # Replace an existing (or commented-out) NAME= line, else append.
    if grep -Eq "^#?$1=" "$CONF"; then
        sed -i -E "s|^#?$1=.*|$1=$2|" "$CONF"
    else
        printf '%s=%s\n' "$1" "$2" >> "$CONF"
    fi
}
set_var WAIT_ONLINE_METHOD ping
set_var WAIT_ONLINE_ADDRESS "$GATEWAY"
set_var WAIT_ONLINE_TIMEOUT "$TIMEOUT"

systemctl enable ifupdown-wait-online.service
# Run it once now to prove the config works (should return within a second).
systemctl restart ifupdown-wait-online.service
systemctl --no-pager --lines=0 status ifupdown-wait-online.service || true
echo
grep -E '^WAIT_ONLINE_' "$CONF"
echo
echo "Done. After the next reboot, check the ordering with:"
echo "  systemd-analyze critical-chain jarvis-api.service"
echo "network-online.target should now sit behind ifupdown-wait-online.service."
