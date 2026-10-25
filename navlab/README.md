# rn-vs-native: NavLab, a navigation performance lab for React Native

This repo is a **pack**. Point it at any React Native + React Navigation codebase:
```bash
node bootstrap.mjs <repo>      # detect, first scan, config + adapter, skills, AGENTS.md block (no app code)
```
then ask the agent in that repo to "install navlab" and "audit navigation". **`AGENTS.md` holds the rules,
the mechanism, the model and the hygiene.** Read it first.

| piece | what |
|---|---|
| `scan/navscan.mjs` | static navigator tree + per-stack cost prediction; adapter learn loop (`node scan/test.mjs` self-check) |
| `probe/navprobe.ts` | runtime shape, per-action timing, journeys, fetch/axios/redux hooks, signposts |
| `lab/journey.py` | real-app journeys under Instruments, measured vs predicted per navigation action |
| `lab/model.py` | per-push cost model (fit / predict), validated leave-one-sweep-out |
| `baseline/` | measured results shipped with the pack (`baseline.py` snapshots a target) |
| `learning/` | gap ledger (`arch/gaps.py add/rank/close/verify`): the pack learns from each repo; see AGENTS.md "Learning loop" |
| `arch/` | evidence tools (see `arch/README.md`): facts, repo profile, contracts, design system, data layer, feature model + intents, depgraph, cochange, replay, portfolio, ledger, release, upgrade, safety net, field perf |
| `skills/` | 14 skills copied into target repos by the bootstrap; the table is in `AGENTS.md § Skills in the pack` |

Hypothesis tested: RN gets slow with too many screens or too many nested navigators.
Answer: neither alone. **Nesting multiplies the cost of every route held by navigators above the screen.**
The lab below is how that was measured.

## The lab

```
app/                    NavLab (Expo prebuild, com.lab.rnnav)
  modules/lab/ios/      LabCore.swift (signposts, hitches, footprint; shared), LabModule.swift (Expo binding + LabProbe view)
  src/nav.tsx           nested navigator tree: depth x kind x registered screens
  src/autopilot.ts      scripted run: startup -> descend -> N pushes -> N pops -> exit
  src/Wireframe.tsx     feed (FlatList) / form (TextInputs) / grid (tiles)
native/                 NavLabNative (xcodegen, com.lab.nativenav): UINavigationController / UITabBarController twin
lab/build.sh [native]   Release build + install (fails loudly; Debug timings are meaningless)
lab/sweep.py            config grid under `xctrace record --template "Time Profiler" --launch`
lab/analyze.py          trace -> signpost intervals x Time Profiler samples -> runs.csv / intervals.csv
lab/report.py           median over reps, ratio vs baseline, CPU buckets, per-k curves, RN vs native
flows/nav-walk.mjs      simwalk regression flow for both arms (ARM=rn|native)
```

## Knobs (launch args)

| arg | default | meaning |
|---|---|---|
| `-depth` | 1 | nested navigators; the leaf one is always a stack |
| `-kind` | stack | non-leaf navigators: `stack`, `tabs`, `mix` (tabs/stack alternating, root is tabs) or `tabroot` (tabs root, stacks below: the common app shape) |
| `-screens` | 3 | registered routes per navigator (RN). UIKit has no route registry: only tab levels get the extra (lazy) tabs |
| `-pushes` | 20 | pushes onto the leaf stack, then the same number of pops |
| `-screen` / `-rows` | mix / 40 | Detail wireframe type (cycles form/grid/feed by k) and size |
| `-freeze` | 0 | RN only: `enableFreeze()` from react-native-screens |
| `-auto` | 0 | run the autopilot (sweeps set this) |

## Signposts (subsystem `lab`, category PointsOfInterest)

Open any `.trace` in Instruments: the Points of Interest track lines these up with the CPU samples.

| signpost | from → to | notes |
|---|---|---|
| `ready` (event) | root screen attached + one CA commit | `ms` since kernel process start (includes dyld) |
| `descend` | `navigate()` root → leaf Home attached + committed | one action through every level |
| `layout` (event) | RN: first JS `onLayout` (shadow tree); UIKit: first `viewDidLayoutSubviews` | `layout_ms` in the analyzer. RN lays a pushed screen out twice |
| `render` | `push()` → new screen's native views attached to the window + one CA commit | RN ends it natively via `<LabProbe>`; the same point on both arms |
| `push` | `push()` → transition end (native-stack `transitionEnd` / `viewDidAppear`) | includes the ~500ms animation |
| `pop` | `goBack()` → RN: React unmount (~8ms, mid-animation); UIKit: `viewDidDisappear` | **not comparable across arms** |
| `hitch` (event) | CADisplayLink frame late by more than half a frame | `ms` late |

`renders` counts screen renders/loads, `nav` counts navigator re-renders (RN, from the navigator `layout` prop) and
`mounted` counts live screens. analyze.py adds per-interval CPU from Time Profiler by thread (JS, main, other) and
by bucket. The first rule that matches any frame of a stack wins: hermes-gc, yoga, text-layout, fabric-mount,
rn-screens, fabric-commit, hermes, app-native, autolayout, uikit-nav, uikit-layout, uikit, ca-commit, dyld.

## Dev loop

```bash
lab/build.sh && lab/build.sh native                                  # Release, both arms
ARM=rn node ~/code/simwalk/simwalk.mjs flows/nav-walk.mjs --repeat 3  # must pass on both arms
ARM=native node ~/code/simwalk/simwalk.mjs flows/nav-walk.mjs --repeat 3
python3 lab/sweep.py one -depth 8 -kind mix -pushes 10 [--arm native] # one traced run + hottest symbols
python3 lab/sweep.py nest --reps 3 --arm both     # nest | screens | rows | stack | grid | smoke; resumable
python3 lab/report.py                             # aggregate every sweep, RN vs native side by side
python3 lab/model.py fit                          # cost model per arm + leave-one-sweep-out error -> traces/sim/model.json
python3 lab/model.py predict --path tabs:4,stack,stack --stack 12 [--screen grid --rows 160]   # expected lag vs native
open traces/nest/depth8_kindmix_pushes10_r1.trace # Instruments: POI track + Time Profiler
```

Change app code → build → flows → re-run sweeps into fresh dirs (move old ones aside) → `report.py`.

## Full suite on a physical iPhone, compared with the simulator

```bash
# phone: unlocked, Developer Mode on, cable, Auto-Lock can stay on (the apps keep the screen awake), charger off if you can
U=<phone udid from `xcrun devicectl list devices`>
UDID=$U lab/build.sh && UDID=$U lab/build.sh native     # first build registers the phone with the Personal Team
python3 lab/sweep.py all --arm both --udid $U            # ~318 runs -> traces/device/, then traces/device/report.txt
python3 lab/report.py --compare traces/sim traces/device # same configs, both arms, both targets
```
Simulator results live in `traces/sim/` (tag `sim`); `--tag` picks another folder. Every run logs the phone's thermal
state at its start and end, and the compare report's `hot` column counts runs that ended above nominal. Re-run those
configs after a cool-down: delete the traces, and the sweep resumes.

## Measurement hygiene (each one bit us)

- **No accessibility client during a sweep.** An attached ax-service (argent or simwalk) turns iOS accessibility
  on, and then UITableView builds every row (40 cells instead of 2) and RN builds AX trees. `sweep.py`
  kills ax-service and argent's simulator-server for the device before every run.
- **Fabric `onLayout` is not "on screen".** It fires from the shadow tree before the main thread mounts anything,
  so whether the native mount lands inside a JS-ended interval depends on timing. Native end points (`LabProbe`) only.
- Each run has a hard timeout (xctrace once wedged 23 min past its own `--time-limit`): kill, delete, retry once.
  Every run logs the host load average: other agents' simulators on this Mac compete for CPU, so absolute
  times carry host noise. The arms are interleaved, so the RN/native ratio is the robust number.
- Release builds only. The first launch after a reinstall is cold (+500ms startup), so sweeps interleave and use medians.
- Single-trace `analyze.py x.trace` only prints; it never overwrites a sweep's CSVs.
- `traces/v1-onlayout/` is the first RN nest sweep: onLayout end points, AX attached. Kept for reference only; don't use it.
