#!/bin/sh
# One time, on the Surface:  sudo sh ~/jarvis/deploy/surface-setup.sh
# Lets user nick restart/stop/start the jarvis-api service (and read its
# status) without a password, so deploy/surface.sh (from the Mac) and
# Claude Code sessions can deploy Core. Nothing else gets passwordless sudo.
# Also turns off the sound chip's power saving (snd_hda_intel power_save=10
# put the codec to sleep after 10 s of silence, so the first sound after a
# quiet spell, the "Hey JARVIS" listen tone, came out late on the desk
# speaker and out of step with the iPads). Safe to run again.
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
printf 'options snd_hda_intel power_save=0 power_save_controller=N\n' > /etc/modprobe.d/jarvis-audio.conf
echo 0 > /sys/module/snd_hda_intel/parameters/power_save 2>/dev/null || true
echo N > /sys/module/snd_hda_intel/parameters/power_save_controller 2>/dev/null || true
echo "✓ sound chip power saving off (now and after reboots)"
echo "✓ $RULE installed: sudo -n systemctl restart jarvis-api now works for nick"
