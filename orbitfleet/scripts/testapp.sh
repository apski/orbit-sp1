#!/bin/bash
# Builds testdata/hello into build/OFHello.app, an lldb-attachable simulator app for the live checks: Apple's own
# simulator apps refuse lldb ("Not allowed to attach"), so P4 needs one of ours. No Xcode project. Plain ad-hoc
# signing is enough to attach; signing with a get-task-allow entitlement wedged the simulator (install stuck as a
# placeholder, every later simctl call on that device hung) on iOS 26.4.
set -euo pipefail
cd "$(dirname "$0")/.."
src=testdata/hello app=build/OFHello.app
if [ -x "$app/OFHello" ] && [ -z "$(find "$src" -newer "$app/OFHello")" ]; then echo "$app"; exit 0; fi
rm -rf "$app" && mkdir -p "$app"
xcrun --sdk iphonesimulator swiftc -parse-as-library -target arm64-apple-ios26.0-simulator -Onone -g \
  "$src/OFHello.swift" -o "$app/OFHello"
cp "$src/Info.plist" "$app/Info.plist"
codesign -s - -f "$app" >/dev/null 2>&1
echo "$app"
