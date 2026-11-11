#!/usr/bin/env bash
# Build Donna for Mac and install it as ~/Applications/Donna.app.
#   mac/DonnaMac/build.sh            # build + install
#   mac/DonnaMac/build.sh --open     # ...and launch it
set -euo pipefail
cd "$(dirname "$0")"

VERSION="1.0"
APP="$HOME/Applications/Donna.app"

swift build -c release --product DonnaMac
BIN="$(swift build -c release --show-bin-path)/DonnaMac"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Donna"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Donna</string>
  <key>CFBundleDisplayName</key><string>Donna</string>
  <key>CFBundleIdentifier</key><string>com.jarvis.donna-mac</string>
  <key>CFBundleExecutable</key><string>Donna</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.lifestyle</string>
  <key>NSHighResolutionCapable</key><true/>
  <!-- Core is plain http on the LAN / Tailscale. -->
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsArbitraryLoads</key><true/></dict>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null
echo "Installed $APP"
[[ "${1:-}" == "--open" ]] && open "$APP"
exit 0
