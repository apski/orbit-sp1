---
name: thread-offload
description: >
  Fit a React Native or native iOS (SwiftUI/UIKit) app to its threading model: find the work that
  makes a user response wait (taps, navigation, gestures, scroll, first frame, startup) on the main
  thread or the RN JS thread, decide per task whether to move it, chunk it or leave it, move it with
  the right platform tool, and prove the move worked. Use when the user says "move off the main
  thread", "off the JS thread", "JS thread busy", "main thread hang", "UI freezes", "tap is slow",
  "jank", "threading model", "worklet", "background thread", "Task.detached", "MainActor", "startup
  JS", or asks why RN needs more perf work than native.
last-updated: 2026-10-24
---

# Thread Offload

One rule on both platforms: **a user response must never queue behind work it doesn't need.**
Native apps and RN apps both break it. What differs is which thread the response waits on, and how
cheap it is to move work off that thread.

- Platform detail: `references/ios.md` (SwiftUI/UIKit), `references/rn.md` (React Native).
  Read the one that matches. An RN app with native screens needs both.
- Measuring loop and harness: `perf-climb` → `rn-perf-climb` / `native-perf-climb`.
- Swift concurrency errors (Sendable, isolation): `axiom-concurrency`.

## 1. The model: which thread does a response wait on?

| Response | iOS native | React Native |
|---|---|---|
| Tap → handler | main | UI thread delivers it, **JS** runs `onPress` |
| Push/pop | main (UIKit), animation in the render server | JS decides, then native-stack runs it natively. JS-stack: JS end to end |
| Edge-swipe back, scroll | main | UI thread (native-stack, ScrollView). New list cells: JS |
| Gesture-driven animation | main / render server | JS, unless Gesture Handler + Reanimated worklets or `useNativeDriver` |
| First frame | dyld + your launch work on main | bundle load + module init + first render on **JS**, then mount on UI |

iOS: one thread does both your UI logic and UI work, and threads are cheap (actors, `Task`, GCD).
RN: your logic, React rendering and JS-driven navigation all share **one** JS thread, and JS has no
cheap threads. That's why RN takes more discipline, not because JS is slow. NavCore measured,
device, Release: with JS idle an RN push was only ~20 ms slower, and Hermes `JSON.parse` +
react-query matched URLSession + `JSONDecoder`. With JS busy, a tap took 1,003 ms vs 19 ms native;
with 1 s of JS start-up work, launch took 1,147 ms vs 160 ms.

## 2. Find the work

1. **List the responses that matter**: launch to first useful screen, the main navigations, list
   scroll, any drag/gesture.
2. **Profile each one** (Release, device for numbers, simulator to locate). For every long task on
   the waited-on thread, write down: what it is, how long, and which response waits.
   - iOS: Instruments Time Profiler + Hangs, Xcode Organizer Hangs, `os_signpost`. See `ios.md`.
   - RN: argent `react-profiler-*` (React + Hermes CPU), a JS lag probe, RN DevTools. See `rn.md`.
3. **Grep for usual suspects** (recipes in each reference). Grep finds candidates; only the
   profile says whether one matters.
4. **Stall test (RN, and native screens in RN apps)**: add a debug knob that busy-loops JS for 3 s,
   then trigger the response. If it waits out the stall, it depends on JS. NavCore's "Block JS 3s"
   + `flows/js-busy.mjs` is the template.

## 3. Decide, per long task

Ask in order. Stop at the first answer that fits.

1. **Does a user response wait on it?**
   - Yes, and it doesn't need UI/React state → move it to a thread the response doesn't use. On
     RN that means native navigation, worklets, `useNativeDriver`. On iOS it means a background
     task.
   - Yes, and it does need UI/React state → keep it there, make it short: virtualize, render less,
     defer the non-urgent part (`startTransition`, a lazy container, after the touch feedback).
2. **Pure computation over ~50 ms** (parse, sort, filter, crypto, image, diff) → background: an
   iOS `@concurrent` function or detached task; an RN native module or worklet runtime. Or chunk
   it and yield between chunks. **Wrapping it in `async`/a Promise/a `Task {}` moves nothing.**
3. **Frequent short work (under ~10 ms)** → leave it. It barely adds wait. Moving it costs more
   in copying and hops than it saves.
4. **Startup work** → never fits under "short", because the first screen waits for all of it.
   Make it lazy (inline requires, defer stores/i18n/analytics/SDK init), or show a native first
   screen that doesn't wait (NavCore layout B / native-first).

Budgets: frame 16.7 ms (60 Hz) / 8.3 ms (120 Hz). A tap feels instant under ~100 ms. Apple counts a
hang at ≥250 ms of main-thread unresponsiveness.

## 4. Move it

Pick from the platform reference. Cross-cutting rules:

- **Only the result crosses back, and it should be small.** On RN, JS objects are always built on
  the JS thread. Parsing 5 MB natively and returning all of it as JS objects saves little. Do the
  filtering/aggregation off-thread and return the slice the screen shows.
- **Don't block the response thread waiting**: no `semaphore.wait`/`DispatchQueue.main.sync` on
  main, no sync TurboModule call that hops to main, no `await` chain that ends in main-actor work.
- **Priority**: awaited work runs at user-initiated priority, prefetch at utility. Waiting on
  lower-priority work is a priority inversion.
- **Off-thread code must not touch UI**: assert it (`dispatchPrecondition`, Main Thread Checker).

## 5. Prove it

- Re-run the same profile and the same flow. The long task is gone from the waited-on thread, and
  the response metric dropped (on-screen marker, not `onAppear`/commit). Many runs, Release,
  median. Use the `perf-climb` harness when the repo has one.
- RN: re-run the stall test. The response should no longer wait out the stall.
- iOS: keep a `dispatchPrecondition(condition: .notOnQueue(.main))` in the moved function so a
  later refactor that pulls it back onto main fails in Debug.
- Report before → after with the run count and configuration. A move with no measured win is
  reverted.

## Gotchas

- `Task { heavy() }` from main-actor code runs `heavy()` on **main**. In a SwiftUI `View`, so does
  `.task { heavy() }`, because `View` is `@MainActor`.
- Xcode 26 targets with "Approachable Concurrency" run `nonisolated async` functions on the
  **caller's** actor. Use `@concurrent`. See `ios.md` §Isolation.
- RN: `async function` / `new Promise` runs synchronously on the JS thread until the first real
  `await`. `setTimeout(0)` only yields between chunks.
- RN: the UI thread can be blocked too: a heavy Reanimated worklet, a big native view tree mounted
  at once, a native module with `requiresMainQueueSetup`.
- Debug timings are meaningless (dev JS is ~3× slower). Simulator inflates launch ~4× and
  AX-timed taps. Locate on the simulator, report numbers from a device in Release.

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = the long task on the waited-on thread, shown in a profile before any move; gate = the same profile and flow rerun with the task gone (device, Release, for numbers); floor = responses moved with a measured win (ready) / long tasks found (breadth); log = `perf/DEVLOG.md`; next = the longest task on a response path.
