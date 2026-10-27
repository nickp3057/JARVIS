#!/bin/sh
# Builds jarvis-tv.apk. Requires Android build-tools 34 and platform 31.
# Secrets (signing key, Plex token/PIN) live outside git in $TV_SECRETS
# (default ~/tcl-tv-cleanup); the signing key must stay the same or the TV
# refuses the upgrade.
set -e
cd "$(dirname "$0")"
SDK=/opt/homebrew/share/android-commandlinetools
BT=$SDK/build-tools/34.0.0
JAR=$SDK/platforms/android-31/android.jar
KS=${TV_SECRETS:-$HOME/tcl-tv-cleanup}/launcher/debug.keystore
./gen-config.sh
rm -rf out && mkdir -p out/classes out/dex
$BT/aapt2 compile --dir res -o out/res.zip
$BT/aapt2 link --manifest AndroidManifest.xml -I $JAR -o out/base.apk out/res.zip
javac --release 11 -Xlint:-options -cp $JAR -d out/classes src/com/tvhome/*.java
$BT/d8 --min-api 31 --lib $JAR --output out/dex out/classes/com/tvhome/*.class
(cd out/dex && zip -q ../base.apk classes.dex)
$BT/zipalign -f 4 out/base.apk out/aligned.apk
$BT/apksigner sign --ks "$KS" --ks-pass pass:android --out out/jarvis-tv.apk out/aligned.apk
echo built out/jarvis-tv.apk
