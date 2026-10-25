---
name: perf-climb
description: >
  Root performance hill-climb for mobile apps. Detects whether the codebase is React Native or
  native iOS (SwiftUI/UIKit), routes to rn-perf-climb or native-perf-climb, and owns the shared
  loop: deterministic benchmarks driven by argent, profiling to find the real gap, one fix per move,
  and a baseline that only moves on a measured win. Use when the user says "make the app faster",
  "perf climb", "performance", "startup time", "screen opens slowly", "scroll jank", "hitches",
  "double fetch", "benchmark before and after", or "find the real performance gap".
last-updated: 2026-10-18
---

# Perf Climb

Make a mobile app faster by measured moves. The harness measures and judges, and you find and fix.
Never claim a speedup the harness didn't accept.

`H="node ~/.claude/skills/perf-climb/scripts/perf.mjs"`. Run it from the app's project root. State
lives in `./perf/`.

## 1. Route

```bash
$H detect            # or: $H detect <dir>
```

| `runtime` | Load | Notes |
|---|---|---|
| `rn` (React Native / Expo, even with an `ios/` project) | `rn-perf-climb` | `root` says which dir to run from in a monorepo |
| `native`, `flavour` swiftui / uikit / mixed | `native-perf-climb` | Simulator only |
| `unsupported` | Stop and tell the user `why` | Flutter and native Android have no climb skill yet |

Load the platform skill now. It covers instrumentation, flows and what to look for. Everything below
is shared.

## 2. The measuring model

- **Marks** in app code: `mark("x:start")` … `mark("x:end")` become a span metric `x`. The 2nd
  occurrence becomes `x#2`, e.g. switching back to a screen.
- **Counts**: requests, body evaluations or renders, scroll jumps, hitches. They don't vary between
  runs, so they're the best metrics to climb: 2→1 fetches is a certain win.
- **Fixtures** (`fixture:*`): input size, e.g. messages loaded. If it differs from the baseline, the
  comparison is refused, because pinned data is part of the ruler.
- **Verdict:** 7 reps plus 1 warmup, a seeded bootstrap 90% CI on the change in medians, and a 5%
  minimum effect. The same input gives the same verdict. `improved` / `regressed` / `noise`.
- **Refusals:** debug vs release runs, changed span definitions, a different platform.

## 2b. Levels: mount the subsystem, not the whole app

Most of a full-flow rep is relaunch and navigation, not the code being optimised. Climb at the lowest
level that reproduces the problem:

| Level | How | Per bench | Trust | Accept? |
|---|---|---|---|---|
| 1 component, no app | `"cmd"`: Jest + `React.Profiler` (RN) or a hosted XCTest (native) prints `METRIC name=n` lines | 2–5s | **counts** only; ms are V8/test-host | no, only a reference point |
| 2 component in the real app | `"route"`: the app mounts the subsystem with fixture data. RN via `globalThis.__perfRoute`, native via a route file in the container | 6–14s | counts **and** timings | yes |
| 3 full flow | `setup` + `flow` (argent) | 25–85s | everything | yes |

- **Prove the bug at the low level first.** Validated on both test apps: an eager `VStack` showed
  `body:Row` 12→3000 at levels 1 and 2 and `chat_open` +813% at level 3. RN `initialNumToRender`
  15→60 showed +300% rows at level 1 and +228% mount at level 2. At level 3 only the timing moved:
  FlatList finishes its batches anyway, so the row count didn't.
- **Each level compares only with itself.** Counts legitimately differ by level (no layout in Jest,
  incremental batches in a full flow).
- **Every move accepted at level 2 must hold at level 3** (`bench all`) before the batch closes.
- Make the subsystem **mountable**: data in, view out (`ChatList(msgs:)`), with fixture data from
  one generator (`PerfFixture` / `perfFixture.ts`) shared by all levels.
- Level-2 routes are test-only. Gate them behind a build flag (native) or a Metro stub (RN), and
  check every release artifact with `$H leakcheck <App.app | export dir>`, which exits 1 on a leak
  (it also finds markers inside Hermes `.hbc`).

## 3. Config `perf/perf.config.json`

```json
{
  "runtime": "rn | native",              // optional; detect fills it
  "platform": "ios",
  "device": "<UDID>",                     // optional if exactly one device is booted
  "devices": ["<UDID>", "..."],           // written by `perf.mjs devices <n>`: reps run in parallel
  "bundleId": "com.acme.app",             // native: required (container, profiler, binary stamp)
  "appProcess": "Expo Go",                // optional: profiler target when several apps run
  "build": "<shell: build && argent run reinstall-app ... absolute .app path>",  // native: strongly advised; "{device}" runs it per device
  "guard": "<shell: exits 0 when behaviour is intact>",  // tests, typecheck, argent QA flow(s)
  "scenarios": {
    "<name>": {
      "setup": "<flow>",                  // starts with launch: → fresh process every rep
      "flow": "<flow>",                   // measured interaction; no launch: if react/profile
      "spans": { "metric": ["start-mark", "end-mark"] },   // "@launch" = host time before flow
      "until": "end-mark",                // wait for this mark instead of a UI-tree gate
      "react": true,                      // RN only: extra reps with React commit/render counts
      "route": "chat?n=3000",             // level 2: mount a perf route instead of a flow ("blank" between reps)
      "tier": "component",                // label: level 2 runs say so in the ledger
      "cmd": "npx jest perf/bench",       // level 1: prints METRIC/FIXTURE/META lines; one process for all reps
      "prepare": "xcodebuild build-for-testing ...",   // level 1: runs before cmd (not with --nobuild)
      "impl": "newia",                    // arm label in the traces (orbit perf compares arms by it)
      "journey": "markets-trade",         // optional; defaults to the scenario name
      "tailMs": 500,                      // keep listening after `until` so post-interactive events land
      "reps": 7, "warmup": 1, "untilMs": 30000
    }
  }
}
```

Gitignore `perf/runs/` and `perf/profiles/`. The harness commits `perf/baseline/`, `perf/stability/` and
`perf/ledger.jsonl` itself.

## 4. The loop: one move per iteration

```
pick → profile → hypothesis → ONE fix → commit → bench → accept | reject + revert → repeat
```

1. **Setup, once per scenario**, in this order, because each step is part of the ruler:
   - `$H guard` must pass.
   - `$H stability <scenario>` benches HEAD twice (A/A). Whatever moves with no code change becomes
     that metric's floor, the minimum change that counts. It's at least 5%. Metrics marked
     `UNSTABLE` are guardrails only; don't climb them.
   - `$H bench <scenario> --label baseline`, then `$H accept <scenario>`. Accept runs the guard and
     commits the baseline.
2. **Pick** the scenario and metric with the biggest user-felt gap. Usually startup, then screen
   open, switch-back, scroll and pagination.
3. **Find the real gap:** `$H profile <scenario>` writes `perf/profiles/<scenario>-<ts>/*.md`. Then
   drill in with argent MCP query tools, using the platform skill's investigation section and
   hunting ground. Profiles find problems; they don't judge fixes.
4. **Hypothesis:** one line, "X costs ~Y because Z (evidence: …)". No evidence means no move;
   profile or count more.
5. **One fix**, committed with the label as its subject. Don't touch the ruler: `perf/`,
   `.argent/flows/`, `perf.ts`, `Perf.swift`, or any `mark(` / `count(` / `fixture(` line.
6. **Bench** in two tiers. Both run `build` first if configured.
   - Iterating: `$H bench <scenario> --quick --label <fix>`. 3 reps, no warmup, about 5s on 3 sims.
     It shows direction and can't be accepted.
   - Deciding: `$H bench <scenario> --target <metric> --label <fix>`. Sequential: it checks after
     each round from 3 reps and stops once the target's verdict can't change and no guardrail is
     drifting the wrong way, otherwise up to 7 reps. Counts usually settle in 1–2 rounds.
   - Plain `$H bench <scenario>` always runs the full reps (used for baselines and `bench all`).
7. **Decide:**
   - Target `improved` past its floor and nothing regressed:
     `$H accept <scenario> --target <metric> --note "<hypothesis>"`. It runs the guard, then commits
     `perf/` itself, as a pathspec-only commit.
   - Improved but a guardrail regressed: if it's off the critical path, use
     `accept … --force --note "<why acceptable>"`. The ledger marks it `forced`.
   - Noise, regressed, or guard failed: `$H reject <scenario> --note "<what, why it didn't move>"`.
     It reverts every commit since the baseline that touches code outside `perf/`, logs, and commits.
     `--keep` logs without reverting. Never revert by hand. The script owns git, and the agent owns
     hypotheses and code.
8. **Cross-check** with `$H bench all` before closing a batch of moves.
9. **Stop** a scenario after 3 rejects in a row, or when it meets the budget.
10. **Report:** `$H report` prints the first baseline vs the current one, plus the move ledger.
    Quote those numbers to the user, e.g. "big chat opens 27% faster, 2→1 fetches".

## 4b. Traces and attribution (Orbit `perf`)

Every bench writes one trace per rep: `perf/runs/<scenario>/<run>/rep-N.jsonl`, with harness meta
(`impl`, `journey`, device, build, commit) first and a `trace:end` stamp last. `accept` commits the
accepted run's traces as `perf/traces/<scenario>/<sha>-<label>/rep-N.jsonl.zst`.

To compare two implementations, bench one scenario per arm (same flow, fixtures and device; a
different `impl`), then run Orbit over both runs' trace directories:

```bash
mkdir -p /tmp/ab && cp -R perf/runs/chat-legacy/<run> /tmp/ab/legacy && cp -R perf/runs/chat-newia/<run> /tmp/ab/newia
agents/orbit/bin/orbit perf /tmp/ab --base legacy --html /tmp/ab/report.html
```

It prints milestones (first_frame, data_ready, interactive, settled), speedups with CIs, and
exclusive phase buckets per screen and journey. It refuses speedups when device, build mode or
fixtures differ. Use the largest bucket as the next hypothesis. App-side API: `native-perf-climb`.

## 5. What accept refuses (the anti-gaming ratchet)

- **Guard fails.** This can't be forced. A faster app that broke behaviour is not a win. Example:
  rendering only 5 of 3000 messages cut `body:Row` by 75% with every other metric clean. Only the
  guard caught it.
- **Level-1 runs as wins.** `accept` on a level-1 run only moves its reference point (`unit-ref`).
- **Quick runs, or runs under 5 reps** unless they stopped decisively on that same `--target`. These
  can't be forced either.
- Target not `improved` past its floor, or any other metric `regressed`, `MISMATCH` (fixture) or `gone`.
- Dirty tree, or a run measured at a different commit than HEAD.
- Ruler edits since the baseline commit: the guarded files above, or changed mark/count/fixture lines.
- Native: the installed binary hash is unchanged even though the code changed (a stale build).

`--force --note` overrides everything except the guard and the rep rules, and is logged.

**Re-baseline:** `accept <run> --force --note "<why>"` with no `--target`, or any run that can't be
compared at all (different parallelism, Debug vs Release,
changed spans) is refused. `accept <run> --force --note "<why>"` then adopts it as a new ruler
epoch: the guard still runs, it's logged as `rebaseline`, no improvement is claimed, and `report`
compares within the current epoch. Use it only for honest re-baselines
("re-baseline: added marks") and judged tradeoffs. Never edit `perf/baseline/*` by hand.

## 6. Guards

The guard is any shell command that exits 0 when behaviour is intact. Make it check what a perf
"fix" is likely to break:

- RN: `npm test -- --silent && npx tsc --noEmit`
- native: `xcodebuild test -scheme App -destination 'platform=iOS Simulator,id=<UDID>' -quiet`
- UI content: `argent flow run .argent/flows/qa-chat.yaml --device <UDID>`. Assert that content
  past the first screen exists, that the right item count shows, and that navigation still works.
  Use `argent-qa-flows` to author it. On SwiftUI, assert with raw `tool: await-ui-element` steps;
  the AX tree only lists on-screen rows.

Chain them with `&&`. Keep it under a minute or two, because it runs on every accept.

## 7. Speed: parallel simulators

`$H devices 3` clones the primary simulator twice (installed apps and data come along), boots all
three, and writes `devices` into the config. Use `{device}` in `build` so each one gets the fresh
build. Each round runs one rep per device concurrently in worker processes.

- Parallelism is part of the ruler. Host contention shifts timings (here `flow_ms` went ~350→450ms),
  so runs at different parallelism are refused. Run `stability` and re-baseline after changing it.
- Measured on the SwiftUI fixture, builds excluded: a full bench took 25s on 1 sim vs 15s on 3, and
  `--quick` took 10s vs 5s.
- Clones persist (`<name> perf-1..n`). Remove them with `xcrun simctl delete <udid>`.
- `profile` and the React work pass use the primary device only.

## 8. Rules

- No speedup claim without an accepted ledger entry.
- One causal change per move.
- If you can't fix flakiness in a flow, the bench isn't trustworthy. More reps don't fix a bad flow.
- Simulator and device actions stay in the main agent. Subagents may read code and profiles only.
- The commands are deterministic. Your judgment goes into the hypothesis and the `--force` notes,
  never into the verdict.

## 9. Harness gotchas (verified 2026-10-17, argent 0.25, Xcode 27)

- Boot simulators **with a window**. Headless boots drop touches.
- `argent run` prints update notices before the JSON; the harness parses from the first `{`.
  Object args go in `--<name>-json '<json>'` or `--args '<full json>'`.
- In flow YAML, a raw tool step is `- tool: <name>` with an `args: {…}` sibling key.
- `xctrace` returns before it is recording. `profile` waits a 3s `--preroll`, then a 1s `--tail`
  after the `until` mark.
- With several apps running, the native profiler needs `app_process`. Native derives it; RN/Expo
  sets `appProcess`.
- Terminate other argent-instrumented apps on the simulator before benching
  (`xcrun simctl terminate <udid> <bundle>`). After a raw `tool:` step the runner auto-detects the
  frontmost app, and a backgrounded instrumented app makes UI reads time out (`Application.getState`).
- `stability` floors = max(5%, 2× the A/A median drift, the A/A CI half-width), so quantised metrics
  like `hitch` get honest floors. `stability` adds floors and changes the ruler. Run it before the baseline, or re-baseline with
  `--force` afterwards. Inflating floors to hide a regression trips the ruler check.
- `reject` reverts only commits since the last settled point (the baseline, or where the last reject
  left the code), so earlier cheat/revert pairs aren't churned again.
- `accept <scenario>` takes the latest run. HEAD may have moved by harness-only commits (`perf/`), so
  benching several scenarios and then accepting each one works.
- Native deep links pop "Open in …?" on every external open, and the alerts queue up in SpringBoard.
  Level 2 uses the route file instead. To clear stuck alerts, reboot the simulator
  (`argent run boot-device --udid <u> --force`). The HEAD check stops a stale run from being accepted.

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = a one-line hypothesis with evidence and its target metric, before the fix; gate = `$H accept` (guard + CI verdict, Release); floor = the accepted baseline per scenario (ready) / scenarios benched (breadth); log = `perf/ledger.jsonl` (`$H report`), plus `perf/DEVLOG.md` for next steps and questions; next = the biggest user-felt gap in `$H profile`.
