---
name: rn-dev-loop
description: >
  Make the React Native iOS dev loop fast, especially with many agents in parallel git worktrees: never rebuild
  native code that did not change. A native fingerprint cache (scripts/rnfp.mjs) reuses the built simulator .app
  across worktrees and JS-only edits (Debug: install as is; Release: re-embed a fresh Hermes bundle in seconds),
  plus the Metro, CocoaPods and Xcode settings that keep the rest warm. Use when the user says "RN build is slow",
  "only build what changed", "fingerprint", "build cache", "skip the native build", "new worktree builds from
  scratch", "pod install every time", "faster dev loop", "rebuild after a JS change", or before an agent runs
  xcodebuild for a React Native app.
last-updated: 2026-10-26
---

# rn-dev-loop: build native only when native changed

A JS edit never needs xcodebuild. A new worktree whose native inputs match a build anyone already made never needs
xcodebuild. Only a change to `ios/`, `Podfile.lock`, a native dependency, `patches/`, Xcode or `RCT_*`/`USE_*` env
does. `rnfp` makes that the default.

## Pick the loop by what changed

| Changed | Do | Cost |
|---|---|---|
| JS/TS only, Debug app already installed | nothing: Metro Fast Refresh (`r` in Metro, or argent `debugger-reload-metro`) | ~1 s |
| Anything, Debug, native inputs seen before (any worktree) | `rnfp build --udid U`: hit, installs the cached .app | seconds |
| JS only, Release (the verification build) | `rnfp build --config Release --udid U`: rebundle (Hermes bytecode into a clone of the cached app) | bundle time only |
| Native inputs new | `rnfp build ...`: miss, xcodebuild with the shared compilation cache, then stored for everyone | full build, once |

Release is the one to verify on (Debug timings are meaningless, and Debug-only checks have missed Release crashes).
The rebundle path makes that cheap enough to do for every JS change.

## rnfp

```bash
F=$(.orbit/home 2>/dev/null || echo ~/code/orbit-sp1)/perf/skills/rn-dev-loop/scripts/rnfp.mjs   # or the repo's .agents/skills copy
node $F hash  [--config Release]          # the fingerprint (same in every worktree with the same native inputs)
node $F build [--config Release] [--udid <claimed sim>] [--scheme S] [-- extra xcodebuild args]
node $F why   [--config Release]          # after a miss: which inputs differ from the newest cached entry
node $F ls                                # cached entries (~/.cache/rnfp/<scheme>/<fp>, APFS clones)
```

Last line is always `rnfp  hit|rebundle|miss  <fp>  <secs>  <app>`. `--root <dir>` if not run from the RN project root.

**What the fingerprint covers:** every git-visible file under `ios/` (minus `build*/`, `Pods/`, `xcuserdata`,
`.xcode.env.local`), `patches/`, the react-native version, every autolinked iOS dependency (published: name and
version; local/monorepo: file contents), `xcodebuild -version`, config, scheme, extra xcodebuild args, and `RCT_*`/
`USE_*` env. Paths are repo-relative, so worktrees at different paths share entries.

**Not covered (force a miss with `--no-store` + a real build, or bump something):** pods referenced by `:path` from
the Podfile outside `ios/`, native code changed inside `node_modules` without a version bump or patch file, env
vars outside `RCT_*`/`USE_*` that the Podfile or a build phase reads, and exports inside the "Bundle React Native
code and images" phase (the rebundle path runs RN's script with defaults; set the same vars in the env).

**Release rebundle:** clones the cached .app, runs RN's own `react-native-xcode.sh` against it (so asset catalogs and
the `-O` Hermes flags match a real build), with the `hermesc` copied from that build's `Pods/hermes-engine` (a hermesc
from npm can emit a different bytecode version and the app dies at launch with "Wrong bytecode version"), then
re-signs ad hoc. Simulator only; device builds need real signing.

**On a miss** it brings Pods in sync only when `Podfile.lock` differs from `Pods/Manifest.lock`: first by cloning
`Pods/` + `build/*-facades` from a sibling git worktree with the same lock (APFS clone) and regenerating codegen,
else `bundle exec pod install` (the Gemfile's pinned CocoaPods; a different CocoaPods rewrites every checksum in
Podfile.lock, which is a new fingerprint). Then it builds arm64 for the generic simulator into `ios/build/rnfp` with
Xcode compilation caching on (see `xcode-build-speed`), refuses to store an .app without a bundle id, and keeps the
newest 6 entries per scheme. One build per worktree at a time (`ios/build/rnfp.lock`): two xcodebuilds on one
DerivedData corrupt each other's products, and that once stored an empty .app.

**Why sibling Pods instead of pod install:** parallel `pod install`s serialize on the CocoaPods download-cache lock
(`~/Library/Caches/CocoaPods/Pods/External/*.lock`); seen here holding another agent's install for 10+ minutes.

## Keep the rest warm

- **Prebuilt core** (`RCT_USE_PREBUILT_RNCORE=1`, `RCT_USE_RN_DEP=1`) is the default since RN 0.84; check
  `ios/Pods/React-Core-prebuilt` exists. Don't switch it off to debug into RN: use `RCT_SYMBOLICATE_PREBUILT_FRAMEWORKS=1`.
  Keep Legacy Architecture removed (`RCT_REMOVE_LEGACY_ARCH`, default since 0.84; 73.0 s -> 58.2 s on the 0.83 template).
- **pod install** only when `Podfile.lock` != `Pods/Manifest.lock` (that is the check Xcode's own "[CP] Check Pods
  Manifest.lock" phase fails on). Codegen runs inside pod install, so skipping one skips the other.
- **Metro**: the transform cache is already shared across worktrees (`$TMPDIR/metro-cache`, keys are repo-relative +
  file hash). Never start Metro with `--reset-cache` as a habit; bump `cacheVersion` in metro.config.js when you
  change babel config instead. With several Metros running, cap `maxWorkers` (default ~75% of cores each), e.g.
  `npx react-native start --max-workers 4`. When an agent is done with a worktree: `watchman watch-del <root>`.
- **One Metro per worktree port**, and the app must point at that port (`RCT_METRO_PORT`) or it will load another
  worktree's JS. Assert state on screen, not presence (see the user's VERIFY rule).

## Android (short)

`org.gradle.configuration-cache=true` and `org.gradle.caching=true` in `gradle.properties`, one shared
`GRADLE_USER_HOME`, `--active-arch-only` for local builds (RN docs: ~75% less native build time), and
`CMAKE_CXX_COMPILER_LAUNCHER=ccache` for the C++ RN tasks Gradle can't cache. Same fingerprint idea applies to the APK;
rnfp is iOS only today.

## Measured here

See `xcode-build-speed` for the compilation-cache numbers. rnfp on NavCore (RN 0.88, prebuilt core), this Mac:
2026-10-26, load average 350-700 from other agents (absolute times inflated, ratios hold):

| Path | Time | Notes |
|---|---|---|
| Debug, cold miss (worktree A) | 952 s | full native build, stored |
| Debug, no-op rebuild in A | 150 s | xcodebuild still walks every target |
| Debug, **hit** in worktree B (same native inputs) | **14 s** incl. `simctl install` | app launched, B's Metro served the bundle |
| Release, cold miss in A | 316 s | |
| Release, JS-only edit in B: **rebundle** | **43 s** | marker string in the new Hermes bytecode, not in the cached one; `flows/rnfp-release-rebundle.mjs` 3/3 pass (RN screen, count 0 -> 1) |

Flow: `BUNDLE=<id> simwalk flows/rnfp-release-rebundle.mjs --udid U --repeat 3`.

## Sources

RN build speed https://reactnative.dev/docs/build-speed · prebuilt core https://expo.dev/blog/precompiled-react-native-for-ios ·
RN 0.83/0.84/0.88 release notes on reactnative.dev/blog · react-native-xcode.sh (v0.88.0) · @expo/fingerprint
https://docs.expo.dev/versions/latest/sdk/fingerprint/ (same idea, Expo-flavoured; does not hash Xcode or env) ·
Rock remote cache https://github.com/callstackincubator/rock (fs-fingerprint, S3/R2/GitHub artifacts) ·
Metro cache https://metrobundler.dev/docs/configuration/
