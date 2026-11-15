#!/bin/sh
# One time, on the Surface:  sudo sh ~/jarvis/deploy/surface-setup.sh
# Lets user nick restart/stop/start the jarvis-api service (and read its
# status) without a password, so deploy/surface.sh (from the Mac) and
# Claude Code sessions can deploy Core. Nothing else gets passwordless sudo.
set -eu
[ "$(id -u)" = 0 ] || { echo "run it with sudo" >&2; exit 1; }
SYSTEMCTL=$(command -v systemctl)
RULE=/etc/sudoers.d/jarvis-deploy
TMP=$(mktemp)
cat > "$TMP" <<RULES
# JARVIS deploys (deploy/surface-setup.sh): restart Core without a password.
nick ALL=(root) NOPASSWD: $SYSTEMCTL restart jarvis-api, $SYSTEMCTL stop jarvis-api, $SYSTEMCTL start jarvis-api, $SYSTEMCTL status jarvis-api
RULES
visudo -cf "$TMP" >/dev/null
install -m 0440 -o root -g root "$TMP" "$RULE"
rm -f "$TMP"
echo "✓ $RULE installed: sudo -n systemctl restart jarvis-api now works for nick"
