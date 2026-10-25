#!/bin/bash
# Android Release build + install (debug-signed; Debug timings are meaningless). Fails loudly: never installs a stale app.
#   lab/android/build.sh [rn|native|both]     default both; SERIAL=<adb serial> (default emulator-5556)
# RN arm: app/android is `expo prebuild --platform android` output; re-run prebuild after native config changes.
set -euo pipefail
S=${SERIAL:-emulator-5556}
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCH=${ARCH:-arm64-v8a}
LOG=$(mktemp)
build() { (cd "$1" && ./gradlew :app:assembleRelease "${@:2}" -q >"$LOG" 2>&1) \
  || { grep -E "error:|e: |FAILURE|What went wrong" -A3 "$LOG" | head -30; echo "BUILD FAILED ($LOG)"; exit 1; }; }
what=${1:-both}
if [ "$what" != native ]; then
  [ -d "$ROOT/app/android" ] || (cd "$ROOT/app" && CI=1 npx expo prebuild --platform android --no-install)
  build "$ROOT/app/android" -PreactNativeArchitectures=$ARCH
  adb -s "$S" install -r "$ROOT/app/android/app/build/outputs/apk/release/app-release.apk" >/dev/null
  echo "installed com.lab.rnnav on $S"
fi
if [ "$what" != rn ]; then
  build "$ROOT/native-android"
  adb -s "$S" install -r "$ROOT/native-android/app/build/outputs/apk/release/app-release.apk" >/dev/null
  echo "installed com.lab.nativenav on $S"
fi
