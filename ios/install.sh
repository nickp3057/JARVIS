#!/bin/zsh
# Build JarvisCompanion once (Release: optimised, no debugger attached, so it
# runs smoother than Xcode's Run) and install it on every connected iPhone
# and iPad, then open it. Devices must be plugged in or on Wi-Fi debugging.
#
#   ios/install.sh            # every connected device
#   ios/install.sh iPad       # only devices whose name contains "iPad"
set -euo pipefail
cd "$(dirname "$0")/JarvisCompanion"

filter="${1:-}"
build=$(mktemp -d)/build
bundle_id=$(xcodebuild -project JarvisCompanion.xcodeproj -scheme JarvisCompanion -showBuildSettings 2>/dev/null \
  | awk -F' = ' '/ PRODUCT_BUNDLE_IDENTIFIER /{print $2; exit}')

echo "Building (Release)…"
xcodebuild -project JarvisCompanion.xcodeproj -scheme JarvisCompanion -configuration Release \
  -destination 'generic/platform=iOS' -derivedDataPath "$build" -allowProvisioningUpdates -quiet build
app=$(ls -d "$build"/Build/Products/Release-iphoneos/*.app | head -1)

json=$(mktemp)
xcrun devicectl list devices --json-output "$json" >/dev/null
devices=$(/usr/bin/python3 - "$json" "$filter" <<'EOF'
import json, sys
data = json.load(open(sys.argv[1]))
for d in data["result"]["devices"]:
    props, hw = d.get("deviceProperties", {}), d.get("hardwareProperties", {})
    if hw.get("reality") != "physical" or hw.get("platform") != "iOS":
        continue
    if d.get("connectionProperties", {}).get("tunnelState") == "unavailable":
        continue
    if sys.argv[2].lower() in props.get("name", "").lower():
        print(f'{d["identifier"]}\t{props.get("name", "")}')
EOF
)
[[ -z "$devices" ]] && { echo "No connected iPhone/iPad found."; exit 1; }

# All devices at once: each install runs in the background.
while IFS=$'\t' read -r id name; do
  (
    if xcrun devicectl device install app --device "$id" "$app" >/dev/null; then
      xcrun devicectl device process launch --device "$id" "$bundle_id" >/dev/null 2>&1
      echo "✓ $name"
    else
      echo "✗ $name (install failed)"
    fi
  ) &
done <<< "$devices"
wait
