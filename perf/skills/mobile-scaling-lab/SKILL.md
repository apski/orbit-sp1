---
name: mobile-scaling-lab
description: "Find where a mobile architecture breaks: build a parametric wireframe app (RN and/or a native UIKit twin) whose structure comes from launch args, instrument it with a shared os_signpost ruler, sweep configs under Instruments (xctrace Time Profiler), and analyze traces into per-knob knee curves with CPU attribution. Use when the user says 'where does RN get slow', 'find the breaking point', 'too many screens', 'nested navigators', 'RN vs native', 'scaling sweep', 'compare architectures', 'signposts + instruments', or wants a dev loop with profiling scripts to answer a perf hypothesis (not to hill-climb a fix: that is perf-climb)."
metadata:
  last-updated: 2026-10-24
  example-repo: ~/code/rn-vs-native
---

# Mobile Scaling Lab

Answer "at what size/shape does X get slow?" with measured curves, not with fixes. The worked example
is `~/code/rn-vs-native`: RN navigation vs a UIKit twin, 306 traced runs, and the knee found where
nesting depth and tab count multiply. Read its README.md before building a new lab.

Related: `perf-climb` / `rn-perf-climb` (improve one app against a baseline), argent skills (driving the sim).

## 1. Design the experiment (before any code)

- **Hypothesis → knobs.** One launch arg per suspected cost (`-depth -kind -screens -rows -pushes -freeze`).
  Always add a knob for *content size*: in the example it outweighed every structural knob by 10–20×.
- **One build per arm** covers every config: the app reads `ProcessInfo.arguments` (RN: export them as a
  module constant). Rebuilds between configs add noise and time.
- **Autopilot, not taps, for measurement**: `-auto 1` runs startup → descend → N actions → undo N → `exit(0)`.
  The exit ends `xctrace record --launch` on its own. argent/simwalk are for *verification* only.
- **A twin arm** (native) turns "is 80ms slow?" into a ratio. Same knobs, ids, wireframes, autopilot and
  signpost names. The twin must be idiomatic and fair (see references/hygiene.md §twin).
- **Counters for the mechanism**, not just times: screen renders, navigator renders (React Navigation:
  navigator `layout` prop, since wrapper components are memoized), mounted screens, memory.

## 2. Build the ruler

- `assets/LabCore.swift`: OSSignposter intervals/events (subsystem `lab`, category PointsOfInterest so
  they line up with Time Profiler), a CADisplayLink hitch monitor, phys_footprint, and time since process start.
  Compile the **same file** into both arms. `assets/LabModule.swift` is the Expo binding plus `LabProbe`.
- **End intervals natively.** RN Fabric `onLayout` fires from the shadow tree *before* the main thread mounts.
  Use a zero-size native probe view (`LabProbe handle={h}`) that ends the interval one runloop turn after
  `didMoveToWindow`. That is the same point the twin uses (first layout + async). Keep onLayout as an event.
- Interval names must be StaticString in OSSignposter: switch over a fixed set.

## 3. Dev loop

```bash
lab/build.sh && lab/build.sh native                  # Release only; fails loudly, never installs a stale app
ARM=rn node ~/code/simwalk/simwalk.mjs flows/<walk>.mjs --repeat 3   # verify both arms after every change
python3 lab/sweep.py one -depth 8 ... [--arm native] # one traced run + hottest symbols
python3 lab/sweep.py <sweep> --reps 3 --arm both     # interleaved arms, resumable
python3 lab/report.py                                # medians, ratios, buckets, per-k curves, RN vs native
```

Assets to copy and adapt: `sweep.py` (grids, BUNDLES/PROCESS), `analyze.py` (BUCKETS regexes, app-native
pattern), `report.py` (AXES), `build.sh`, `nav-walk.mjs`. Explore new screens once with argent
(`argent-device-interact`), then save a simwalk flow. Every navigating tap needs an `until`.

## 4. Analyze

- Per interval: wall ms, CPU per thread (JS / main / other), CPU per bucket. The first bucket rule that
  matches **any** frame wins, ordered specific → generic (hermes-gc, yoga, text-layout, fabric-mount,
  rn-screens, fabric-commit, hermes, app-native, autolayout, uikit-nav, uikit-layout, uikit, ca-commit).
  Leaf-first matching mislabels work, e.g. `UIView init` under Fabric's mounting lands in "uikit".
- Report the median over reps per config, the ratio vs the sweep's first config, and RN/native per config.
- Find the knee by a ratio jump, then explain it with counters and buckets: "nav renders = depth, hermes +X".
- Before trusting any number, sanity-check anomalies (a smaller config slower than a bigger one) and
  split by sub-variable (here: wireframe type cycles with k).

## 4b. Turn the curves into a predictor

`assets/model.py fit <root>` fits, per arm, `y = a + b·navigators + c·tab_routes + d·stack_len` for push JS CPU and
render ms on per-config medians, and validates leave-one-sweep-out (fit on 4 sweeps, predict the 5th). Screen
content is a lookup table from the rows sweep. `model.py predict --path tabs:4,stack,stack --stack 12` gives the
expected per-push lag vs native in ms and frames. Fit per target: simulator coefficients don't transfer to a
device, so `sweep.py all` refits `<tag>/model.json`. Report the held-out error with every prediction. A term
learned from only one sweep extrapolates badly; here the stack term went from 3ms to 17ms error when held out.

Calibration (sim, RN 0.88 / React Navigation 7): JS ≈ 23 + 1.55·navigators + 0.075·tab_routes + 0.50·stack_len ms;
render ≈ 59 + 1.35·nav + 0.083·tabs + 0.65·stack ms; native render ≈ 68 + 0.34·nav + 0.13·stack. Predicted the
depth-8 × 300-tab config at 173ms against 185ms measured.

## 5. Measurement hygiene (every item bit the example; details in references/hygiene.md)

1. **No accessibility client during measurement.** An attached ax-service (argent/simwalk, which lives 1h)
   turns iOS AX on: UITableView then built 40 cells instead of 2. `sweep.py` kills it before every run;
   argent 0.30+ socket is `/tmp/ax-<udid8>`, simwalk's is `/tmp/sd-<udid8>`.
2. **Host load gate.** Other agents' simulators drove load to 134. Wait for the 1-minute load average to drop
   below 10 before a run, discard the run if it rises past 15, and log the load per run.
3. **xctrace wedges** about 1 run in 15 and records nothing (a 40KB bundle). Detect "app never launched" at 30s and
   "app exited but xctrace alive" at 45s, then stop it with SIGINT, keep the trace in `traces/hung/`, retry ×3.
4. **Interleave arms per config**, so drift hits both equally; ratios are the robust number.
5. **Release builds only**; the first launch after reinstall is cold (+500ms). Medians absorb it.
6. **Never let tooling clobber data**: single-trace analysis prints only; archive superseded sweeps.
7. **Heavy analysis during a sweep** runs at background QoS: `taskpolicy -b python3 lab/analyze.py ...`.

## 6. Report

Lead with the answer to the hypothesis, then one table per knob with RN / native and the knee marked.
State the mechanism with evidence, a rule of thumb, what users actually feel (does the total exceed the
animation?), and the caveats: metrics that aren't comparable across arms, simulator vs device, host noise.

## Troubleshooting

- **The analyzer sees a smaller config as slower than a bigger one.** Check whether the interval end lands
  before or after the main-thread mount, and whether a sub-variable (screen type) is mixed into the median.
- **`build.sh` printed "installed" after errors.** Use pipefail and fail on any xcodebuild error. A new
  Swift file in an Expo local module needs `pod install`.
- **A flow re-taps Back and double-pops.** The back button reappears in place, so pass `until`.
- **`describe` shows another app's tree.** A transition frame was captured; describe again.
- **Native-devtools RPC times out.** Use a temporary NSLog + `simctl spawn <udid> log show` instead.
