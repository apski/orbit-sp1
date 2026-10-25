---
name: native-perf-climb
description: >
  Hill-climb a native iOS app's (SwiftUI or UIKit) performance with argent and the perf-climb
  harness: Perf.swift marks read from the simulator container, deterministic counters (body
  evaluations, cell configs, requests, frame hitches), startup from kernel process start,
  Instruments profiling via argent, and a ratcheted baseline. Use when the codebase is Swift/Xcode
  and the user says "make the app faster", "SwiftUI performance", "UIKit performance", "launch
  time", "hitches", "slow list", "scroll jank", or perf-climb detect returns native.
last-updated: 2026-10-18
---

# Native Perf Climb (SwiftUI / UIKit)

The loop, the config schema, and the accept rules live in **`perf-climb`**. Read it first. This
skill covers the native specifics.

`H="node ~/.claude/skills/perf-climb/scripts/perf.mjs"` · drop-in `~/.claude/skills/native-perf-climb/assets/Perf.swift`
· where bugs hide: `~/.claude/skills/native-perf-climb/references/hunting-ground.md`.
· what to move off the main thread and how: `thread-offload`.

Simulator only: the harness reads the app container, and argent profilers reject physical
iPhones. Timings reflect the host Mac, so climb on the simulator and confirm big wins on hardware
with Instruments.

## Setup

1. **Perf.swift.** Copy it into the app target. Call `Perf.start()` first: in `App.init()` for
   SwiftUI, or at the top of `application(_:didFinishLaunchingWithOptions:)` for UIKit. It records:
   - `process:start`, the kernel process start time, so startup includes dyld and pre-main work.
   - `meta.debug`. Debug and Release runs are never compared.
   - A CADisplayLink hitch monitor, enabled when `defaults perf=YES`. `bench` sets this. It yields
     `count:hitch` and `count:hitch:ms`.
2. **Marks and counters:**

   | Want | SwiftUI | UIKit |
   |---|---|---|
   | first usable screen | `.onAppear { Perf.mark("app:interactive") }` on the first real content | end of first `viewDidAppear` with data |
   | screen open | `Perf.mark("chat:press")` in the button action, `Perf.mark("chat:ready")` in `.onAppear` of the first data row | press handler → `viewDidAppear`, or the first `cellForRowAt` after reload |
   | render work | `let _ = Perf.count("body:ChatRow")` inside `body` | `Perf.count("cell:ChatCell")` in `cellForRowAt`/`cellForItemAt`, `Perf.count("layout:X")` in `layoutSubviews` |
   | requests | `Perf.count("net:GET /chats/:id/messages")` in the API layer, once, centrally | same |
   | input size | `Perf.fixture("messages", msgs.count)` | same |
   | scroll jumps | `Perf.count("scroll:jump")` when the content offset changes >200pt without a drag | same, in `scrollViewDidScroll` |
   **Spans and milestones (for `orbit perf` attribution):**

   | Want | Call |
   |---|---|
   | screen start | `Perf.milestone("trade", "tap")` in the action |
   | phase work across callbacks | `let id = Perf.begin("coordinator", phase: "navigation", screen: "trade")` … `Perf.end(id)` |
   | scoped work | `Perf.span("model:prepare", phase: "data") { … }`; async: `await Perf.span(…) { await … }` (thread `async`) |
   | first frame | `.onAppear { Perf.firstFrame("trade") }`, the next vsync, not the callback |
   | interactive | `Perf.firstFrame("trade", as: "interactive")` on the primary control, once it has real data |
   | data ready | `Perf.milestone("trade", "data_ready")` where the first real row appears |

   Phases: `navigation`, `data`, `render`, `layout` (config in `[perf].phases`). A span without a
   phase inherits its parent's. Main-thread time no span covers shows up as `main:uninstrumented`
   (from the runloop busy monitor, on when `defaults perf=YES`). That is the bucket to instrument
   or profile next. Spans are also OSSignposter intervals (subsystem `perf`), so Instruments shows them.
3. **Build step** (`"build"` in the config). Build **Release** for the simulator, then reinstall
   with an **absolute** `.app` path:
   ```
   xcodebuild -project App.xcodeproj -scheme App -configuration Release -sdk iphonesimulator \
     -destination 'platform=iOS Simulator,id=<UDID>' -derivedDataPath build -quiet build && \
   argent run reinstall-app --udid <UDID> --bundleId <id> --appPath "$PWD/build/Build/Products/Release-iphonesimulator/App.app" >/dev/null
   ```
   With several simulators, put `{device}` in place of the UDID (both `-destination` and `--udid`); bench runs it once per device. With a workspace or CocoaPods, use `-workspace`. Ignore the
   `IDERunDestination: Supported platforms … empty` log line; it's harmless.
4. **Flows.** Argent's flow runner reads the **UIView hierarchy**, and pure SwiftUI content inside a
   hosting view has no UIViews. So a SwiftUI flow selector (`tap: { id: … }`, `await: { visible: … }`)
   finds nothing even though `describe` (the AX tree) shows the element. For SwiftUI screens:
   ```yaml
   steps:
     - launch: com.acme.app
     - tool: await-ui-element
       args: { condition: visible, selector: { identifier: chat-row-big }, timeoutMs: 10000 }
   ```
   ```yaml
   steps:                       # measured flow: tap by coordinate taken from `describe`
     - tool: gesture-tap
       args: { x: 0.5, y: 0.141 }
   ```
   End the measured flow with `"until": "<end mark>"` in the config, not a UI gate. A heavy screen
   (thousands of eager rows) makes the AX read time out
   (`ViewInspector RPC timed out: Application.getState`), but the mark still arrives. UIKit views
   resolve fine with normal `tap: { id: … }` selectors.
5. **Config** example:
   ```json
   { "platform": "ios", "bundleId": "com.acme.app", "build": "…",
     "scenarios": {
       "startup":   { "flow": "perf-launch", "until": "app:interactive",
                      "spans": { "startup": ["process:start", "app:interactive"], "launch_to_ui": ["@launch", "app:interactive"] } },
       "open-chat": { "setup": "perf-home", "flow": "perf-open-chat", "until": "chat:ready",
                      "spans": { "chat_open": ["chat:press", "chat:ready"] } } } }
   ```
   Prefer `startup` (from process start) over `launch_to_ui`, which includes argent's launch
   overhead.

Then baseline and climb per `perf-climb`.

## Levels 1 and 2 (see perf-climb §2b)

- **Mountable subsystem:** a view that takes data (`ChatList(msgs:)`), plus `PerfFixture` for the
  pinned data used by every level.
- **Level 1, `assets/PerfUnitTests.swift`:** copy it into a unit-test target hosted by the app
  (xcodegen: `type: bundle.unit-test`, depending on the app, and add it to the scheme's
  `testTargets`). The app needs `ENABLE_TESTABILITY: YES`. The test hosts the view in a window and
  reads counts with `Perf.capture { }`. Config:
  `"prepare": "xcodebuild build-for-testing … -configuration Release -derivedDataPath build-test -quiet"`,
  `"cmd": "xcodebuild test-without-building … -derivedDataPath build-test -only-testing:<Target>/PerfUnitTests/<test>"`.
  **Don't put `-quiet` in `cmd`:** it hides the test's METRIC output. About 4–5s per bench.
- **Level 2, `assets/PerfRoutes.swift`:** wire it on the root view (see the file header). Build perf
  builds with `"SWIFT_ACTIVE_COMPILATION_CONDITIONS=\$(inherited) PERF_ROUTES"` in `build`. The
  scenario is `{ "route": "chat?n=3000", "until": "chat:ready", "spans": { "chat_mount": ["route:open", "chat:ready"] } }`.
  The harness writes the route file and launches the app if needed. No URL scheme is involved.
- **Release check:** build without the flag, then run `$H leakcheck path/to/App.app`, which must be clean.

## Finding the gap

- `$H profile <scenario>` runs Instruments through argent (CPU, hangs, leaks) and writes `native.md`.
- Drill down with `profiler-stack-query`: `hang_stacks` (hang_index), `thread_breakdown`,
  `function_callers` (with `thread: "main"`), `leak_stacks`.
- **On the simulator, hangs are often detected but unattributed** (the stack comes back empty). When
  that happens, the counters are your evidence. `count:body:Row = 3000` on a screen that shows 15 rows
  is the bug. Then read the code with `references/hunting-ground.md`.
- Counters you don't have yet are cheap probes. Add `Perf.count` in a scratch commit, bench, read
  the number, then drop or keep it. Adding a counter is a ruler change: re-baseline with
  `accept --force --note "re-baseline: added counter X"` before climbing on it.
- For deeper SwiftUI analysis, record manually:
  `xcrun xctrace record --template 'SwiftUI' --device <UDID> --attach <App> --time-limit 10s`, or
  use the `Animation Hitches` and `App Launch` templates. These are evidence for the hypothesis,
  not the verdict.

## Gotchas (verified 2026-10-17)

- (2026-10-18) The busy monitor's sleep observer must run **after** Core Animation's commit
  (`CFIndex.max` order). Ordered first, SwiftUI layout in the commit read as idle (2/7 reps).
- (2026-10-18) An async `Perf.span` from MainActor needs `#isolation` inheritance under Swift 6
  (built into the template). The `withValue(_:operation:isolation:)` overload is deprecated in Xcode 27.

- A failed build that leaves the old binary installed makes every fix read as noise. The `build`
  step fails loudly, and accept refuses when the binary hash didn't change but the code did.
- `perf.jsonl` is append-only across reps. The harness only uses events after each rep's t0, so no
  reset is needed. `bench` deletes the file at start.
- `Perf.count` from `body` is a lock plus a dictionary increment, flushed every 200ms. It's fine
  even at thousands per second.
- Swift 6 strict concurrency: Perf.swift compiles clean (`nonisolated(unsafe)` statics,
  `@MainActor` start and hitch monitor).
- `com.apple.*` apps can't be flow targets.

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: the loop's rules are `perf-climb`'s (accept = gate, accepted baseline = floor, `perf/ledger.jsonl` = log). Native-specific: adding a counter is a ruler change, re-baselined with `accept --force --note` before climbing on it.
