# PerfStack: journey-level comparative profiling

Status: v1 built 2026-10-18 (attribution, comparison, report, iOS instrumentation, harness bridge). The execution layer (CPU samples per bucket) is specified, not built. Contract style follows `agents/orbit/SPEC.md`.

## 1. What it is

PerfStack measures one user journey in two implementations and explains the difference. It goes from the whole journey down to the screen, the phase and the span, and (next) to the functions that ran.

It exists to make claims like this one, with the evidence behind them:

> On the same device and fixtures, Next opens Chat 8.9× faster than legacy (685 → 77 ms p50, 90% CI 8.4–10.6×) and cuts main-thread busy time by 91%. On legacy, 660 ms of the 685 is one main-thread stretch that no span covers, between the data load and the first frame. That stretch is layout of 3000 eager rows.

That sentence is the real result from the validation run (section 11), not an illustration.

PerfStack is not a profiler. Profilers find hot code. PerfStack attributes user-felt latency to architecture: navigation, data, render, layout, and time spent waiting. It also checks that two measurements are comparable before it calls one faster.

| Question | Where it is answered |
|---|---|
| How much faster is B than A, end to end? | journey window, `interactive` speedup with CI |
| Which screen got faster, and at which milestone? | screen windows: first frame, data ready, interactive, settled |
| Where did the time go? | exclusive phase buckets that sum to tap → interactive |
| What ran concurrently and was hidden? | `overlapped` per phase; timeline lanes per thread |
| How long was the main thread blocked? | `main_busy` (runloop observer), not only instrumented spans |
| Is the comparison fair? | refusals: device, build mode, platform, journey, fixtures |
| Which functions? | execution layer (section 12, next) |

Non-goals: CI-level regression gating (perf-climb owns accept/reject), and production RUM (the analytics tool owns field data).

## 2. The measurement hierarchy

```
Journey     markets → trade              app time = Σ screen windows; wall time reported, never a speedup
  Screen    trade                        tap → first_frame → data_ready → interactive → settled
    Phase   navigation | data | render | layout | main:uninstrumented | waiting
      Span  coordinator, model:prepare, decode:orderbook, fetch:orderbook (thread, parent, self time)
        Execution  CPU samples, call stacks, hangs (Instruments; joined by time window — section 12)
```

Every event carries its time, and spans carry thread, parent, phase and screen. The arm (`impl`), journey, device and build come from the harness's meta line. That is enough to line activity up across threads, and across the JS/native boundary when a second clock exists (section 4.2).

### 2.1 Milestones: definitions and how each is measured

| Milestone | Definition | Measured by | Error |
|---|---|---|---|
| `tap` | The user's action. Cold launch: kernel process start (`process:start`, renamed `launch:tap`). | `Perf.milestone(screen, "tap")` in the action handler | Excludes touch-to-handler latency (~1 frame) |
| `first_frame` | The first display frame that carries the new screen | `Perf.firstFrame(screen)` in onAppear/viewDidAppear: the target timestamp of the next CADisplayLink tick | ±1 frame. Lifecycle callbacks alone are not a frame. |
| `data_ready` | The first real data is on screen | `Perf.milestone(screen, "data_ready")` where the first data row appears | Lifecycle time, not presentation |
| `interactive` | The primary control is on screen, with real data, and the frame carrying it was presented | `Perf.firstFrame(screen, as: "interactive")` on the control's appearance | ±1 frame. A control that is not enabled yet is not interactive. |
| `settled` | First moment at or after interactive followed by 50 ms with no main-thread work | Computed by Orbit from spans and busy stretches. Needs the harness's `trace:end` stamp to prove idle tails. | Busy stretches under 2 ms read as quiet |

`interactive` must be backed by a probe. The flow that ends the rep should act on the control (tap it, await its result). If the probe fails, the rep is invalid, never fast. The mark is the timer and the probe is the gate. Setting the mark when an effect runs is not "interactive".

## 3. Attribution

### 3.1 The rule

For each window (one screen's tap → interactive), cut time at every span and busy-stretch boundary. In each elementary interval, the first match wins:

1. **The deepest main-thread span.** Its phase (inherited from the nearest ancestor that has one; `other` if none) gets the time, and the span gets self time.
2. **Main busy with no span:** `main:uninstrumented`. The main thread was working on something nobody named.
3. **The deepest background or async span:** its phase. Main was idle and waiting on that work.
4. **Nothing:** `waiting`.

Invariants (tested):
- **Exclusive.** Buckets sum to the window (`test_buckets_sum_to_the_window_in_every_report_window`).
- **Overlap is never added to wall time.** Work hidden behind the winner is reported as `overlapped[phase]` (`test_overlap_is_reported_never_added`).
- **Priority is fixed** (`test_priority_main_span_then_main_busy_then_background_then_waiting`).
- **Phases inherit down the tree; the deepest span wins** (`test_deepest_main_span_wins_and_phase_inherits`).

### 3.2 Why main first

Interactivity on iOS is gated by the main thread: no frame and no touch handling happen while it is busy. Background work only costs the user wall time when main is idle and waiting on it. Rule 3 encodes that. Its ceiling: when main is idle and two background spans run, the deepest one wins, which is not necessarily the one main is waiting on. True critical path needs dependency edges (a span declaring what it waits on). Add them when `overlapped` shows large parallel background phases.

### 3.3 Main busy, not just spans

Spans only see what someone instrumented. `BusyMonitor` (in `Perf.swift`) observes the main runloop:
- a wake observer (`afterWaiting`) with order `CFIndex.min`;
- a sleep observer (`beforeWaiting`) with order `CFIndex.max`.

The order matters. Core Animation's commit, where SwiftUI layout and rendering happen, is itself a `beforeWaiting` observer. A sleep observer ordered before it calls that work idle. Validation caught exactly this: 2 of 7 legacy reps showed 384 ms of "waiting" that was really layout (section 11).

A busy stretch is only known when it ends, so every main-thread mark cuts the open stretch. Busy time up to a milestone is then always flushed with that milestone.

## 4. Trace format

### 4.1 Events

One JSONL file per rep per arm. Schema: `agents/orbit/schemas/trace-event.schema.json`. Times are host-epoch ms (the simulator shares the host clock).

| `k` | Fields | Written by |
|---|---|---|
| `meta` | `impl`, `journey`, `scenario`, `device`, `platform`, `debug`, `commit`, `build`, `parallel` | Harness first line (it wins); app adds `debug` |
| `b` / `e` | `id`, `p` (parent), `n`, `ph`, `th` (`main`, `bg:<qos>`, `async`), `sc` / `id` | `Perf.begin` / `Perf.end` / `Perf.span` |
| `m` | `n` = `<screen>:<milestone>` or any mark | `Perf.milestone`, `Perf.firstFrame`, `Perf.mark`; harness `trace:end` |
| `busy` | `th`, `t`, `d` | `BusyMonitor` |
| `c` / `f` | `n`, `v` | `Perf.count` / `Perf.fixture` (fixtures must match across arms) |
| `sync` | `src`, `t0`, `t1`, `tn` | The non-native runtime (JS) |

Async spans record their thread as `async`. Awaiting does not occupy main, so while one is suspended attribution reads "waiting on data", not main-thread work.

### 4.2 Clock synchronisation (RN and any second clock)

Native events need no conversion. Events from another clock carry `src` (for example `js`), and that runtime must emit sync samples. In each sample, the JS side records `t0` before a bridge call and `t1` after it, and native records `tn` inside the call.
- The offset is `tn − (t0 + t1) / 2`.
- Orbit uses the sample with the smallest round trip (NTP's best-sample filter), and reports its half-RTT as `clock.<src>.error_ms`.
- Events from a source with no sync samples are dropped and reported as an error, never placed on a guessed clock (`test_js_clock_uses_the_tightest_sync_sample`, `test_unsynced_unclosed_and_orphan_events_are_errors_not_silence`).

With JSI's synchronous calls the error is microseconds. With the async bridge it is half the round trip, which is why the tightest sample is used.

### 4.3 Malformed traces

Errors are reported, never silent:
- An end without a begin is dropped.
- An unclosed span is closed at the trace end and flagged.
- An unknown parent is treated as a root and flagged.
- An unsynced clock drops that source's events.

## 5. Comparison

- **Arms** are `meta.impl` values. The base comes from `--base`, else `[perf].base`, else the first by name.
- **Refusals.** If arms differ in `device`, `debug`, `build`, `platform`, `journey` or fixture values, the report says `comparable: false`, lists why, and prints **no speedups** (`test_mismatched_device_refuses_speedups`).
- **Statistics.**
  - p50 is the lower median, which is always an observed rep.
  - p95 is nearest-rank, flagged `p95_low_n` under 20 reps, where it is the max rather than a tail estimate.
  - The speedup is p50(base) / p50(arm), with a seeded bootstrap 90% CI (2000 resamples, seed 0). The same traces give the same interval.
- **The bar shows the median rep**, chosen by interactive time. Bucket medians are also reported, but they do not sum to the total; the median rep's buckets do.
- **Journey.** App time is the sum of the screen windows. Wall time (which includes think time and harness steps) is reported but never gets a speedup.

## 6. Orbit integration

| Piece | Where |
|---|---|
| Command | `orbit perf <traces-dir|file> [--base A] [--html out.html] [--json]` |
| Analysis | `agents/orbit/orbit/perf.py`: L3 over **evidence**, not the index. It starts no processes (R7) and its output is byte-stable (R3). |
| Config | `[perf]` in `orbit.toml`: `base`, `phases` (bucket order), `main`, `labels` |
| Schemas | `schemas/trace-event.schema.json` (input), `schemas/perf-report.schema.json` (output, version 1) |
| Route | `perf` in `routes.toml` (words: perf, speedup, faster, latency, attribution, traces, ...), 3 labelled questions |
| Tests | `tests/test_orbit.py` class `Perf` (11 tests), golden `tests/golden/perf.json`, fixture `tests/fixture/perf/{legacy,next}/rep-{1,2,3}.jsonl` |
| Report | Self-contained HTML: no scripts and no network, light and dark themes. Per window: a milestone table with CIs, exclusive bars, and timelines per arm on one time scale, with lanes per thread and depth plus a `main busy` lane. |

The fixture is designed to reproduce the widget's numbers: Trade is 840 → 350 ms, 2.4×, split 130/170/310/230 vs 40/100/110/100. The golden pins that output.

## 7. Instrumentation

### 7.1 iOS (built): `native-perf-climb/assets/Perf.swift`

```swift
Perf.milestone("trade", "tap")                                     // in the action
let nav = Perf.begin("coordinator", phase: "navigation", screen: "trade")
Perf.end(nav)                                                      // in the destination's onAppear
let book = await Perf.span("fetch:orderbook", phase: "data") { await api.orderbook() }  // async: thread "async"
let vm = Perf.span("model:prepare", phase: "data") { Model(book) }  // sync; nested spans parent automatically
.onAppear { Perf.firstFrame("trade") }                             // skeleton frame
.onAppear { Perf.firstFrame("trade", as: "interactive") }          // on the primary control, with data
```

Spans are also `OSSignposter` intervals (subsystem `perf`, Points of Interest), so an Instruments recording shows the same spans next to CPU samples. The busy and hitch monitors run only when `defaults perf=YES`, which the harness sets. The file compiles clean under Swift 6 strict concurrency with Xcode 27. The async span inherits the caller's isolation (`#isolation`), so MainActor code can pass non-Sendable closures.

### 7.2 React Native (specified)

`perf.ts` writes the same events with `src: "js"`, plus a `sync` sample at startup and at each navigation: one call to a native module that returns `tn`. Hermes sampling and React Profiler commits stay as execution-layer evidence (perf-climb `react: true`).

### 7.3 Android (specified)

Use the same events from `Trace.beginSection`-wrapped calls. Use Perfetto for the execution layer, FrameTimeline for `first_frame`, and Macrobenchmark as the harness.

## 8. The experiments

### 8.1 A/B journey

Run the same scenario in both arms, with identical fixtures, API responses, device and build mode. For example, the arms can be a **legacy** UI and its replacement (**next**) during a migration.

### 8.2 Isolating where a win comes from

| Benchmark | What it proves |
|---|---|
| Legacy shell → legacy screen vs legacy shell → Next screen | The screen implementation's advantage |
| Legacy shell → Next screen vs Next shell → Next screen | Navigation and shell overhead |
| Cold launch → interactive, legacy vs Next | End-to-end user experience |

For an RN → native migration, replace "legacy" with RN. Hybrid journeys (RN → native → RN) are the case that needs section 4.2.

A fast screen mounted inside a slow shell is still slow for the user. The second row is the one that catches it.

## 9. Evidence and history

- `perf.mjs bench` writes one trace per rep: `perf/runs/<scenario>/<run>/rep-N.jsonl` (gitignored).
- `perf.mjs accept` (baseline, accept, re-baseline) commits the accepted run's traces to `perf/traces/<scenario>/<sha>-<label>/rep-N.jsonl.zst` (zstd -19 when the CLI exists). `orbit perf` reads `.jsonl.zst` on Python ≥ 3.14 (stdlib `compression.zstd`; `test_zst_traces_read_the_same`).
- Every accepted improvement therefore has its traces at the commit it was measured on. `orbit perf perf/traces/<scenario>` across two directories reproduces the report.
- **Ruler rule:** the harness treats `perf/traces/` as bookkeeping, and treats changed `span(`, `milestone(`, `firstFrame(` and `Perf.begin(` lines as ruler edits, like `mark(`.

## 10. Agent loop

`perf-climb` owns the loop: one move, benched, then accepted or rejected. PerfStack gives it the next target:

1. `orbit perf <traces>`, then take the largest bucket of the slowest screen.
2. If it is `main:uninstrumented`, add spans there (a ruler change: re-baseline), or record Time Profiler for that window (section 12).
3. Write a hypothesis naming the bucket ("render 310 ms because eager stack, evidence `body:Row` 3000").
4. Make one fix, bench, and accept or reject.

A skill that automates the diagnosis step is still to do (section 13).

## 11. Validation

**Fixture.** 42/42 Orbit tests pass (30 existing plus 12 new). A mutation check that swaps rules 2 and 3 fails `test_priority_...`.

**Real simulator run**, 2026-10-18, iPhone 17 Pro sim (iOS 26.4), Release builds. The test app is a SwiftUI chat opening 3000 messages, with legacy = eager `VStack` and next = `LazyVStack`. 7 reps + 1 warmup per arm, through `perf.mjs bench`, then `orbit perf`:

| ms (p50) | legacy | next | speedup, 90% CI |
|---|---|---|---|
| data ready | 436.5 | 57.6 | 7.58× [7.03–8.39] |
| first frame | 685.0 | 76.8 | 8.92× [8.44–10.62] |
| interactive | 685.0 | 76.8 | 8.92× [8.44–10.62] |
| settled | 743.7 | 76.8 | 9.68× [9.17–11.53] |
| main thread busy | 676.1 | 60.5 | 11.18× [10.4–12.38] |

The median-rep buckets for legacy are navigation 12.5, data 0.2, `main:uninstrumented` 660.2 and waiting 12.2, which sum to 685.0.

**Bugs found by validation, now fixed:**
- **Busy observer order.** 2/7 legacy reps showed 384 ms "waiting": the layout ran in Core Animation's commit, after the sleep observer. Fixed by ordering the observers (section 3.3). After the fix, busy ≈ tap → interactive on every rep.
- **Truncated busy stretches.** A stretch still open when the harness read the file was lost. Fixed by cutting on marks, plus a 500 ms harness tail (`tailMs`).
- **Idle tails could not prove `settled`.** Fixed with the harness `trace:end` stamp.
- **Swift 6:** the async span needed caller-isolation inheritance.

**Seen in the data:** legacy's first frame is not earlier than interactive. The skeleton frame cannot present while main lays out 3000 rows. That is a real finding, and PerfStack shows it directly.

## 12. Execution layer (next build)

**Goal:** expand a bucket or span into the functions that ran in it.

1. `perf.mjs profile <scenario>` already records Instruments through Argent (Time Profiler, hangs). Add an `xctrace export` of the `time-profile` table for that run.
2. Use a new trace event kind `sample` (`t`, `th`, `stack` as frame ids) plus a frame table. Do not store it in `facts` (determinism rule: evidence lives in runs).
3. Orbit joins samples to attribution by timestamp. Each sample inherits the bucket and span of its interval, which gives top self and total frames per bucket and per span.
4. In the report, clicking a timeline span lists its top frames, with `file:line` where symbols resolve.

**Done when:** for the validation app, the legacy `main:uninstrumented` bucket's top frames name SwiftUI layout under `ChatList`, and the next bucket does not.

## 13. Build order from here

1. **Execution layer** (section 12).
2. **Instrument the app.** Add `Perf.swift` behind a `PERF` flag; mark the most-used journeys with milestones and phases.
3. **The section 8.2 matrix** for those journeys, using a debug launch hook that opens a route directly in each arm.
4. **Physical device confirmation** of the headline numbers. The simulator measures the Mac; Argent drives a physical iPhone, but the container read needs a device file pull.
5. **`perfstack` agent skill:** run `orbit perf`, name the largest bucket, propose one controlled experiment, then hand off to perf-climb.
6. **RN `perf.ts` sync** and **Android**: only if those apps return.

## 14. Ceilings (known, deliberate)

| Simplification | Ceiling | Upgrade |
|---|---|---|
| Main-first sweep | The deepest background span stands in for the one main waits on | Dependency edges between spans |
| Attribution is O(cuts × spans) | Slow beyond ~10k spans per window | Interval tree |
| `firstFrame` is the next vsync deadline | ±1 frame | Instruments display track |
| Busy stretches < 2 ms dropped | Sub-2 ms work reads as idle | Lower `BusyMonitor.minMs` |
| Bootstrap CI on medians, n = 5–7 | Wide when the arms overlap; p95 is the max | ≥ 20 reps for tail claims |
| Simulator | Measures the host Mac | Item 4 above |
