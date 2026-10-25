#!/bin/bash
# Startup lab variants: the RN app rebuilt with G synthetic JS modules bundled (app/src/startup/gen.mjs),
# installed side by side as com.lab.rnstartup.g<G> so sweeps can interleave bundle sizes. Ends by
# rebuilding the stock app (no synthetic modules) onto com.lab.rnnav, so the navigation lab is untouched.
#   lab/build_startup.sh [G ...]        default: 2000 8000 (sweep.py arms rn-g2k, rn-g8k)
set -euo pipefail
U=${UDID:-55BAFC60-D9C9-457C-B068-B12D63A832D8}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app/ios/build/Build/Products/Release-iphonesimulator/NavLab.app"
TMP=$(mktemp -d)
trap 'node "$ROOT/app/src/startup/gen.mjs" 0 >/dev/null' EXIT  # never leave thousands of generated files behind
for G in ${@:-2000 8000}; do
  node "$ROOT/app/src/startup/gen.mjs" "$G"
  "$ROOT/lab/build.sh"
  cp -R "$APP" "$TMP/g$G.app"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.lab.rnstartup.g$G" "$TMP/g$G.app/Info.plist"
  codesign --force --sign - --timestamp=none "$TMP/g$G.app" 2>/dev/null
  xcrun simctl install "$U" "$TMP/g$G.app"
  echo "installed com.lab.rnstartup.g$G (bundle $(du -h "$TMP/g$G.app/main.jsbundle" | cut -f1))"
done
node "$ROOT/app/src/startup/gen.mjs" 0
"$ROOT/lab/build.sh"
echo "stock app (0 synthetic modules, bundle $(du -h "$APP/main.jsbundle" | cut -f1)) back on com.lab.rnnav"
rm -rf "$TMP"
