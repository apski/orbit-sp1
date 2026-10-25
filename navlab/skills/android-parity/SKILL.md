---
name: android-parity
description: "Extend the NavLab measurements to Android: build the RN NavLab app and a Kotlin/Jetpack Compose twin with the same knobs and autopilot, trace sweeps with Perfetto (atrace sections as the shared ruler), fit an Android cost model next to the iOS one, and flag every iOS-only conclusion. Use when the user says 'Android', 'does this hold on Android', 'Compose twin', 'Perfetto', 'platform parity', or before deciding a migration for a cross-platform app."
---

# Android parity

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check android-parity` (the repo profile from the install; it asks only for what this skill is missing).

Every number in the pack so far is iOS. RN on Android has a different cost profile: a different JS engine
configuration per device class, ART, Choreographer/RenderThread, and no UIKit. **Until this skill has run, label every
NavLab conclusion iOS-only.**

Status: **designed, not yet built or measured.** The steps below are the plan; build them in order and keep the
same rules as the iOS lab (`$NAVLAB_HOME/AGENTS.md` hygiene, mobile-architecture-lab pipeline).

## 1. Environment (this machine has the SDK, no images yet)
- `sdkmanager "system-images;android-35;google_apis;arm64-v8a"`, then an AVD (Pixel 8 profile). Prefer a physical mid-range
  device for reported numbers; emulators are good for direction only.
- RN arm: `npx expo prebuild --platform android`, then a Release build (`./gradlew assembleRelease`, debug-signed for the lab).

## 2. The shared ruler
- RN: extend the Lab module with an Android implementation using `android.os.Trace.beginAsyncSection(name, cookie)` /
  `endAsyncSection` (async sections cross threads, like signposts), the same names (`push`, `render`, `navaction`...)
  and the same message fields encoded in the section name. `LabProbe` ends `render` in `onAttachedToWindow` plus one
  `Choreographer` frame (the counterpart of the iOS probe).
- Twin: Kotlin + Compose, Navigation-Compose with nested `NavHost`s for depth, `NavigationBar` for tabs, the same
  wireframes (LazyColumn feed, a form, LazyVerticalGrid and non-lazy variants), the same launch args via intent extras
  and the same autopilot.
- Frames: `FrameMetricsAggregator` or the `gfxinfo framestats` jank count during each push window.

## 3. Sweeps and analysis
- Record with `perfetto` (config: atrace categories `view gfx am dalvik` + app tracing, plus CPU scheduling, 10ms
  sampling), launched through `adb shell am start` with the knob extras. The autopilot ends with `finish()` + `System.exit`.
- `trace_processor` SQL over the `slice` table pulls the lab's sections, with per-thread CPU time from `sched` inside
  each section. That replaces `analyze.py`'s xctrace export; keep the same CSV columns so `report.py` and `model.py`
  run unchanged.
- The same sweeps: nest, screens, rows, stack, grid, app, probecal. `model.py fit` writes `traces/android-<device>/model.json`.

## 4. Report
`report.py --compare traces/sim traces/android-<device>`. Say which conclusions hold on both platforms and which
are iOS-only. Feed the per-platform gap into `rn-native-migration` (a surface can be worth migrating on one
platform and not the other).

## Verification
Same as iOS: an argent flow on both Android arms (argent supports Android emulators), A/A runs for floors,
interleaved arms, and a quiet host.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill android-parity --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
