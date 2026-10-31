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
adb -s "$TV" shell pm grant com.tvhome android.permission.RECORD_AUDIO
# Never doze the launcher: its alert stream must survive standby (StayReachable).
adb -s "$TV" shell dumpsys deviceidle whitelist +com.tvhome >/dev/null
# Lets the home screen read and control what Spotify is playing.
adb -s "$TV" shell cmd notification allow_listener com.tvhome/com.tvhome.MediaListener
# The service isn't exported; BootReceiver starts it for any explicit broadcast.
adb -s "$TV" shell am broadcast -n com.tvhome/.BootReceiver >/dev/null
# Notes for future sessions, kept on the TV itself.
adb -s "$TV" push ../README.md /sdcard/JARVIS_TV.md >/dev/null
echo "deployed to $TV"
