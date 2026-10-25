#!/bin/bash
# Release build + install. Debug timings are meaningless. Fails loudly: never installs a stale app.
#   lab/build.sh [native]                 simulator (UDID, default the lab sim)
#   UDID=<phone udid> lab/build.sh [native]   physical iPhone: iphoneos, automatic signing, devicectl install
# TEAM defaults to the free Personal Team, so no App IDs get registered in a company account.
set -euo pipefail
U=${UDID:-55BAFC60-D9C9-457C-B068-B12D63A832D8}
TEAM=${TEAM:-HYY86J479Q}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG=$(mktemp)
# Physical UDIDs look like 00008130-000144523E62001C; simulators are UUIDs.
if [[ $U =~ ^[0-9A-F]{8}-[0-9A-F]{16}$ ]]; then
  SDK=iphoneos; SIGN=(-allowProvisioningUpdates DEVELOPMENT_TEAM=$TEAM CODE_SIGN_STYLE=Automatic CODE_SIGNING_ALLOWED=YES)
else
  SDK=iphonesimulator; SIGN=()
fi
DEST=${DEST:-id=$U}  # DEST=generic/platform=iOS builds+signs without a connected phone
build() { xcodebuild "$@" -configuration Release -sdk $SDK -destination "$DEST" -derivedDataPath build ${SIGN[@]+"${SIGN[@]}"} -quiet build >"$LOG" 2>&1 \
  || { grep -E "error:" "$LOG" | head -20; echo "BUILD FAILED ($LOG)"; exit 1; }; }
if [ "${1:-}" = native ]; then
  cd "$ROOT/native"
  [ NavLabNative.xcodeproj -nt project.yml ] || xcodegen generate -q
  build -project NavLabNative.xcodeproj -scheme NavLabNative
  APP=build/Build/Products/Release-$SDK/NavLabNative.app
else
  cd "$ROOT/app/ios"
  [ Pods/Pods.xcodeproj -nt ../modules/lab/ios ] || pod install >/dev/null   # new native files need pod install
  build -workspace NavLab.xcworkspace -scheme NavLab
  APP=build/Build/Products/Release-$SDK/NavLab.app
fi
[ "$DEST" = "id=$U" ] || { echo "built $APP (not installed: DEST=$DEST)"; exit 0; }
if [ $SDK = iphoneos ]; then
  xcrun devicectl device install app --device "$U" "$APP" >/dev/null
else
  xcrun simctl install "$U" "$APP"
fi
echo "installed $APP"
