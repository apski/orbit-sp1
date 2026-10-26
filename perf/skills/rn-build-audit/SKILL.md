---
name: rn-build-audit
description: >
  Audit a React Native repo (or a native iOS one) for dev-loop build waste and fix it LOCALLY: a read-only scan
  (scripts/audit.mjs) ranks findings with file:line evidence (dSYM in Debug, symbol uploads on every build, script
  phases with no outputs, compilation cache off, prebuilt RN core off, stale Pods, Metro reset-cache, Android ABIs and
  Gradle caches, cold worktrees, stale DerivedData), then applies fixes that live only on this Mac: git-ignored env
  files, an xcodebuild -xcconfig overlay, user-level Gradle properties, .git/info/exclude. Nothing reaches release
  builds, so hacks are fine. Use when the user says "audit the build", "why is the RN build slow", "build
  optimisations", "speed up builds locally", "dev build too slow", "analyze this repo's build", or when starting work
  in an RN repo whose build nobody has looked at.
last-updated: 2026-10-26
---

# rn-build-audit: find build waste, fix it on this Mac only

Goal: the local Debug / simulator loop as fast as it can be. Release, archive and CI behaviour stay exactly as
committed. Hacky is allowed when it is local and reversible.

## 1. Scan (read-only, ~1 s)

```bash
A=<this skill dir>/scripts/audit.mjs
node $A <repo or RN app dir>          # ranked: [high|med|low] id  file:line / why / fix
node $A <dir> --json                   # same, for tooling
```

Run it on the RN app dir (the one whose package.json depends on `react-native`). At a monorepo root it runs the
iOS checks and prints `rn-app-inside <dir>` for each RN app below. Works without git (walks the tree).

| id | What it means | Local-only fix |
|---|---|---|
| `ios-debug-dsym` | Debug runs `dsymutil` every build, never cached | `-xcconfig fast-sim.xcconfig` (dwarf) |
| `ios-upload-in-debug` | Sentry/Crashlytics/... phase uploads on every build | `export SENTRY_DISABLE_AUTO_UPLOAD=true` in `ios/.xcode.env.local` (RN Sentry scripts read it) |
| `ios-script-every-build` | script phase with no outputs runs on every build | `[ -n "$LOCAL_FAST" ] && exit 0` hack (below) |
| `ios-compilation-cache-off` | each worktree / clean build recompiles everything | `-xcconfig fast-sim.xcconfig` (shared CAS + prefix mapping) |
| `ios-prebuilt-core-off` | RN core compiled from source | `RCT_USE_PREBUILT_RNCORE=1 RCT_USE_RN_DEP=1 pod install` |
| `ios-pods-stale` | Pods out of sync | `rnfp build` (clones Pods from a sibling worktree) |
| `worktrees-cold-native` | same native app compiled per worktree | `rnfp build` (fingerprint cache, `rn-dev-loop`) |
| `metro-reset-cache` | Metro transform cache discarded each start | start Metro without it (local script) |
| `android-all-abis` | native code built for 4 ABIs | `reactNativeArchitectures=arm64-v8a` in `~/.gradle/gradle.properties` |
| `android-caching` / `-configuration-cache` / `-parallel` | Gradle redoes work | `org.gradle.<x>=true` in `~/.gradle/gradle.properties` |
| `derived-data-stale` | DerivedData of deleted checkouts | the printed `rm -rf` |

Also: `ios-flipper`, `ios-force-bundling`, `ios-use-frameworks`, `ios-hermes-off`, `ios-ccache-and-cas`,
`metro-max-workers`, `watchman-missing`.

## 2. Fix locally: the toolkit

Never edit committed files for a local-only fix unless the user says so. Use, in this order:

1. **Command-line overlay**: `xcodebuild ... -xcconfig <xcode-build-speed>/fast-sim.xcconfig`. Applies to every
   target, Pods included, above project settings. Put the full command in a git-ignored script (5).
2. **`ios/.xcode.env.local`**: git-ignored by the RN template and sourced by RN's script phases through
   `with-environment.sh`. Good for `SENTRY_DISABLE_AUTO_UPLOAD=true`, `LOCAL_FAST=1`, `NODE_BINARY`.
3. **Process env at pod install**: `RCT_USE_PREBUILT_RNCORE=1 RCT_USE_RN_DEP=1 RCT_REMOVE_LEGACY_ARCH=1 pod install`.
4. **`~/.gradle/gradle.properties`**: user-level properties override the project's. It is machine-wide, which is fine
   for a dev Mac (arm64 emulators).
5. **Local files nobody else sees**: list them in `.git/info/exclude` (per clone, never committed), e.g. a
   `dev-build.sh` that wraps xcodebuild with the overlay, or a `metro.local.sh` with `--max-workers 4`.
6. **Hack a committed file locally** only when 1-5 can't reach it (a script phase without outputs, a Podfile line):
   edit it, then `git update-index --skip-worktree <file>` so it never shows in diffs or commits. Record it in the
   report: `git ls-files -v | grep '^S'` lists them, and `--no-skip-worktree` undoes it. Pulls touching that file
   will refuse until it is undone. Typical hack for an always-running phase: put `[ -n "$LOCAL_FAST" ] && exit 0`
   as its first line and set `LOCAL_FAST=1` in `ios/.xcode.env.local` (if the phase does not source it, export it
   in the dev-build script).

When the user wants a fix committed instead (like ExampleApp's Debug `dwarf` and Release-only Sentry upload), gate on
`$CONFIGURATION` so Release stays identical, and build Debug once to prove nothing breaks (a dSYM listed as a script
input is fine when missing: verified on ExampleApp).

## 3. Prove it

Before/after on the same machine and load: a no-op build, a one-file edit build, and for worktrees a fresh-checkout
build. Same command both times, only the fix differs. Report the three numbers and the load average. Then, if UI is
involved, the user's rule applies: install on a claimed simulator and run the flows on a Release build.

## Self-check

`node tests/audit.test.mjs`: every detector fires on `tests/fixture`, gated and echo-only phases stay quiet.
On ExampleApp at HEAD before 2026-10-26 it reported `ios-debug-dsym` and `ios-upload-in-debug`, both since fixed.

Related: `rn-dev-loop` (fingerprint cache, `rnfp`), `xcode-build-speed` (overlay, compilation cache).
