#!/bin/sh
# Undo the TV cleanup.
#   ./restore.sh            restore removed apps AND the Google TV home screen
#   ./restore.sh <package>  restore a single app
S=${TV:-10.0.0.46:5555}
adb connect $S >/dev/null
if [ -n "$1" ]; then adb -s $S shell cmd package install-existing "$1"; exit; fi
while read p; do adb -s $S shell cmd package install-existing "$p" </dev/null; done < "$(dirname "$0")/removed.txt"
adb -s $S shell pm enable com.google.android.tungsten.setupwraith
adb -s $S shell pm enable com.google.android.apps.tv.launcherx
adb -s $S shell pm uninstall com.tvhome
echo "Also revoke the TV app's adb access: TV Settings > System > Developer options >"
echo "Revoke USB debugging authorizations (this also un-pairs this Mac)."
