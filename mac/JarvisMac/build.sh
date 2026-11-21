#!/usr/bin/env bash
# Build JARVIS for Mac and install it as ~/Applications/JARVIS.app.
#   mac/JarvisMac/build.sh            # build + install
#   mac/JarvisMac/build.sh --open     # ...and launch it
set -euo pipefail
cd "$(dirname "$0")"

VERSION="2.0"
APP="$HOME/Applications/JARVIS.app"

swift build -c release --product JarvisMac
BIN="$(swift build -c release --show-bin-path)/JarvisMac"

# Quit a running copy so the new build replaces it cleanly.
pkill -x JARVIS 2>/dev/null || true

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/JARVIS"

# The reactor icon, drawn by the same renderer as the phone's (ios/icon).
ICONWORK="$(mktemp -d)"
if swift ../../ios/icon/render_icon.swift "$ICONWORK" >/dev/null 2>&1; then
  SET="$ICONWORK/JARVIS.iconset"
  mkdir -p "$SET"
  for px in 16 32 128 256 512; do
    sips -z $px $px "$ICONWORK/AppIcon-Mac.png" --out "$SET/icon_${px}x${px}.png" >/dev/null
    sips -z $((px * 2)) $((px * 2)) "$ICONWORK/AppIcon-Mac.png" --out "$SET/icon_${px}x${px}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/JARVIS.icns"
fi
rm -rf "$ICONWORK"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleDisplayName</key><string>JARVIS</string>
  <key>CFBundleIdentifier</key><string>com.jarvis.mac</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>CFBundleIconFile</key><string>JARVIS</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <!-- Core is plain http on the LAN / Tailscale. -->
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsArbitraryLoads</key><true/></dict>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1
echo "Installed $APP"
if [[ "${1:-}" == "--open" ]]; then open "$APP"; fi
