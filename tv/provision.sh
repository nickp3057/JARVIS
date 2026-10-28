#!/bin/sh
# Give the TV its own scoped Core token (never JARVIS_API_TOKEN) and Core's URL.
# Scopes: events:read, status:read, chat (the remote's "Jarvis, ...") and
# settings:write (the Settings page saves the shared colour theme).
# Run from the Mac at home:  TV=10.0.0.46:5555 tv/provision.sh
# Re-running rotates the token. The TV finds Core again by itself if the
# Optiplex's DHCP address changes.
set -e
TV=${TV:-10.0.0.46:5555}
CORE_HOST=${CORE_HOST:-$(ssh jarvis "hostname -I | cut -d' ' -f1")}
TOKEN=$(ssh jarvis 'cd ~/jarvis && .venv/bin/python -m app.client_tokens create nicks-room-tv events:read status:read chat settings:write' | tail -1)
case "$TOKEN" in jc_*) ;; *) echo "token creation failed" >&2; exit 1;; esac
adb connect "$TV" >/dev/null
adb -s "$TV" shell am broadcast -n com.tvhome/.ProvisionReceiver \
  --es core "http://$CORE_HOST:8000" --es token "$TOKEN" | tail -1
echo "TV provisioned against http://$CORE_HOST:8000"
