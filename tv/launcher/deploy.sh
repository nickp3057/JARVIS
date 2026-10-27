#!/bin/sh
# Build, install and restart JARVIS TV, then re-apply what a reinstall resets.
#   TV=10.0.0.46:5555 ./deploy.sh
set -e
cd "$(dirname "$0")"
TV=${TV:-10.0.0.46:5555}
./build.sh
adb connect "$TV" >/dev/null
adb -s "$TV" install -r out/jarvis-tv.apk
# TCL's boot manager blocks background starts unless allowed (reset on reinstall).
adb -s "$TV" shell appops set com.tvhome APP_AUTO_START allow
adb -s "$TV" shell appops set com.tvhome SYSTEM_ALERT_WINDOW allow
adb -s "$TV" shell pm grant com.tvhome android.permission.READ_LOGS
# The service isn't exported; BootReceiver starts it for any explicit broadcast.
adb -s "$TV" shell am broadcast -n com.tvhome/.BootReceiver >/dev/null
echo "deployed to $TV"
