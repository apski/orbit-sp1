---
name: xcode-build-speed
description: >
  Make local iOS simulator builds (native Swift/SwiftUI or the native half of React Native) fast and light across many
  worktrees and agents: one shared Xcode compilation cache with prefix mapping, a no-edit xcconfig overlay
  (fast-sim.xcconfig), shared SwiftPM clones, no dSYM in the loop, arm64 only, DerivedData hygiene and a concurrency
  cap. Use when the user says "Xcode build is slow", "every worktree builds from scratch", "DerivedData is huge",
  "compilation cache", "speed up xcodebuild", "incremental build", "build only what changed", "switching schemes
  rebuilds everything", or before an agent runs xcodebuild in a fresh checkout.
last-updated: 2026-10-26
---

# xcode-build-speed: compile each thing once per machine

Xcode keys DerivedData by checkout path, so every worktree, `/tmp` copy and `-derivedDataPath` starts cold and
recompiles everything, SwiftPM packages included. Since Xcode 26 the fix is the **compilation cache**: compiler
outputs keyed by content in a CAS that any build can read. It is off by default, and with `-derivedDataPath` its CAS
moves inside that dir (measured: `COMPILATION_CACHE_CAS_PATH = <dd>/CompilationCache.noindex`), so it must be pointed
at one shared path explicitly.

## The overlay (no project edits)

`fast-sim.xcconfig` (next to this file) turns it all on for every target, Pods included, at command-line precedence:

```bash
X=<this skill dir>/fast-sim.xcconfig
xcodebuild -scheme App -configuration Debug -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath build/dd -xcconfig $X \
  -clonedSourcePackagesDirPath ~/Library/Developer/Xcode/SourcePackages.shared -skipPackageUpdates build
```

What it sets and why:

| Setting | Why |
|---|---|
| `COMPILATION_CACHE_ENABLE_CACHING = YES` | reuse Swift + C-family compiles across clean builds, branches, worktrees |
| `COMPILATION_CACHE_CAS_PATH = ~/Library/Developer/Xcode/CompilationCache.shared` | one CAS for all worktrees, whatever `-derivedDataPath` says |
| `CLANG_/SWIFT_ENABLE_PREFIX_MAPPING`, `..._PROJECT_PREFIX_MAPPING = YES` | strip SDK/toolchain/checkout paths from cache keys; without them another checkout path hits only ~80% (swift forums) |
| `DEBUG_INFORMATION_FORMAT = dwarf` | dSYM generation (`dsymutil`) runs every build and is never cached; lldb on the simulator reads dwarf from the .o files |
| `ARCHS = arm64`, `ONLY_ACTIVE_ARCH = YES` | a generic simulator destination otherwise builds arm64 *and* x86_64 (2x) |

Never use it for archives, TestFlight or device builds (no dSYM, arm64-sim only). Release-for-simulator verification
builds are fine.

**Not cached by design**: run-script phases (the RN bundle phase, SwiftLint), linking, asset catalogs, dSYM, codesign.
Those are the floor of a "fully cached" build; keep script phases' input/output files declared so they can be skipped.

## Packages

- `-clonedSourcePackagesDirPath <one shared dir>`: package checkouts once per machine, not per worktree.
- `-skipPackageUpdates` / `-onlyUsePackageVersionsFromResolvedFile`: never re-resolve in the loop (resolve on purpose).
- Macro / plugin packages: `-skipMacroValidation -skipPackagePluginValidation` avoid the trust prompt in headless builds.

## Hygiene and concurrency

- Each worktree copy leaves 1-4 GB of DerivedData behind (this Mac had 15 `<App>-*` dirs, ~25 GB, most from
  `/tmp` copies). With the shared CAS those dirs are disposable: delete DerivedData of worktrees that no longer exist
  (`plutil -extract WorkspacePath raw <dd>/info.plist`, then check the path), never the CAS.
- Cap the CAS: `COMPILATION_CACHE_LIMIT_SIZE` (bytes) or `COMPILATION_CACHE_LIMIT_PERCENT` if disk is tight.
- N agents x `xcodebuild` default parallelism oversubscribes the cores; on a busy machine pass `-jobs <cores/N>`.
- ccache (RN `:ccache_enabled => true`) is an alternative for C++ pods, not an addition: it replaces `CC`, and
  nobody has measured it stacked with Xcode's cache. Use the compilation cache first; try ccache (with `base_dir` set
  to the common parent of the worktrees and `hash_dir = false`) only if pod C++ still recompiles.

## "Scheme changes" without pain

- Settings an agent wants to vary (cache, dwarf, archs, flags) go in an xcconfig passed with `-xcconfig`, not in the
  pbxproj. Nothing to merge, nothing to revert, and it applies to all schemes.
- Shared schemes live in `xcshareddata/xcschemes`; choose with `-scheme`, config with `-configuration`. Debug and
  Release keep separate intermediates, so switching between them does not invalidate the other.
- Changing a build setting on the command line (or toggling the overlay) changes cache keys once; after that both
  variants are cached.
- Adding files: keep the target on synchronized folders (`PBXFileSystemSynchronizedRootGroup`, Xcode 16+) so new
  files need no pbxproj edit; for generated projects (xcodegen/Tuist) regenerate only when `project.yml` is newer.

## Measured here

A production iOS app (native SwiftUI, 11 SwiftPM packages), Debug, arm64 simulator, 2026-10-26, load average 350-710 from
other agents (absolute times inflated; the hit counts are load-independent):

| Build | Time | Cache hits / misses | Load |
|---|---|---|---|
| fresh copy, cache off | 676 s | n/a | 705 |
| fresh copy, filling the cache | 1132 s | 0 / 367 | 597 |
| **another path**, warm cache, no prefix mapping | 497 s | 200 / 167 (54%) | 443 |
| **another path**, warm cache, `fast-sim.xcconfig` (prefix mapping) | **175 s** | 254 / 166 (60%) | 694 |
| no-op rebuild | 23 s | | 438 |
| one-file edit, `dwarf-with-dsym` | 146 s | | 353 |
| one-file edit, `dwarf` | **81 s** | | 397 |

The first build after switching dSYM -> dwarf took 188 s (changed flags invalidate everything once). ~40% of
compiles still miss from another path even with prefix mapping: not yet explained (candidates: SwiftPM checkouts
and DerivedData-relative module paths; check `-showBuildSettings` / the misses in the log before tuning further).
That app now commits `dwarf` for Debug and runs the Sentry dSYM upload in Release only.

## Sources

Xcode 26 release notes (compilation caching) · Bitrise compilation cache FAQ and blog (19-55% on OSS iOS projects,
CI) https://docs.bitrise.io/en/bitrise-build-cache/build-cache-for-xcode/xcode-compilation-cache-faq · swift forums
"shared cache across machines" (170 s -> 61 s clean; ~80% hits from another directory)
https://forums.swift.org/t/about-swift-shared-cache-across-machines/81850 · Tuist Xcode cache (prefix mapping)
https://tuist.dev/en/docs/guides/features/cache/xcode-cache · settings verified present in Xcode 27.1 Clang.xcspec /
Swift.xcspec.
