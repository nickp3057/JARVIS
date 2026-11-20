#!/bin/zsh
# Build JarvisCompanion once (Release: optimised, no debugger attached, so it
# runs smoother than Xcode's Run) and install it on every connected iPhone
# and iPad, then open it. Devices must be plugged in or on Wi-Fi debugging.
#
#   ios/install.sh            # every connected device
#   ios/install.sh iPad       # only devices whose name contains "iPad"
#
# Wi-Fi installs drop now and then ("Connection reset by peer"), usually
# when a device was asleep, so each device gets a few tries. The build log
# goes to a file; only errors are printed.
set -euo pipefail
cd "$(dirname "$0")/JarvisCompanion"

filter="${1:-}"
work=$(mktemp -d)
build="$work/build"
build_log="$work/build.log"
bundle_id=$(xcodebuild -project JarvisCompanion.xcodeproj -scheme JarvisCompanion -showBuildSettings 2>/dev/null \
  | awk -F' = ' '/ PRODUCT_BUNDLE_IDENTIFIER /{print $2; exit}')

echo "Building (Release)…"
if ! xcodebuild -project JarvisCompanion.xcodeproj -scheme JarvisCompanion -configuration Release \
    -destination 'generic/platform=iOS' -derivedDataPath "$build" -allowProvisioningUpdates \
    build >"$build_log" 2>&1; then
  grep -E "error:" "$build_log" | grep -v "failed with exit code 0" | sort -u | head -20 || true
  echo "✗ Build failed (full log: $build_log)"
  exit 1
fi
app=$(ls -d "$build"/Build/Products/Release-iphoneos/*.app | head -1)
echo "✓ Built $(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$app/Info.plist") ($(/usr/libexec/PlistBuddy -c 'Print CFBundleVersion' "$app/Info.plist"))"

list_devices() {
  local json=$(mktemp)
  xcrun devicectl list devices --json-output "$json" >/dev/null 2>&1 || true
  /usr/bin/python3 - "$json" "$filter" <<'EOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(0)
for d in data["result"]["devices"]:
    props, hw = d.get("deviceProperties", {}), d.get("hardwareProperties", {})
    if hw.get("reality") != "physical" or hw.get("platform") != "iOS":
        continue
    if d.get("connectionProperties", {}).get("pairingState") != "paired":
        continue  # someone else's phone that's merely visible
    if d.get("connectionProperties", {}).get("tunnelState") == "unavailable":
        continue
    if sys.argv[2].lower() in props.get("name", "").lower():
        print(f'{d["identifier"]}\t{props.get("name", "")}')
EOF
}

devices=$(list_devices)
[[ -z "$devices" ]] && { echo "No connected iPhone/iPad found."; exit 1; }

# One device at a time: parallel installs over Wi-Fi drop more often.
failed=0
while IFS=$'\t' read -r id name; do
  ok=0
  for attempt in 1 2 3 4; do
    log="$work/install-$id.log"
    if xcrun devicectl device install app --device "$id" "$app" >"$log" 2>&1; then
      xcrun devicectl device process launch --device "$id" "$bundle_id" >/dev/null 2>&1 || true
      ok=1
      break
    fi
    reason=$(grep -m1 -oE "NSDescription = .*|Connection reset by peer|device is locked|could not be established" "$log" || echo "install error")
    echo "  … $name: try $attempt failed ($reason), retrying"
    # Poking the device brings its Wi-Fi tunnel back up.
    xcrun devicectl device info details --device "$id" >/dev/null 2>&1 || true
    sleep $((attempt * 5))
  done
  if (( ok )); then
    echo "✓ $name"
  else
    echo "✗ $name (gave up; unlock it, keep it on Wi-Fi, and run: ios/install.sh \"$name\")"
    failed=1
  fi
done <<< "$devices"
exit $failed
