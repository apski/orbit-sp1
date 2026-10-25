# NavLab: React Native navigation performance lab

NavLab answers "is our navigation structure making the app slow, where, and by how much?" for any
React Native + React Navigation codebase. It has three parts:
- a **measured baseline**: a synthetic RN app and its UIKit twin, swept over 300+ traced runs;
- a **cost model** fitted from that baseline;
- **tools** that map a real codebase onto the model (`navscan`), observe it live (`navprobe`), and measure it (`journey.py`).

This file holds the rules and knowledge every agent working with NavLab must follow. Skills:
`skills/navlab-install` (fit the lab to a repo), `skills/navlab-audit` (find and explain hot paths) and
`skills/design-afresh` (find a feature's real boundaries and redesign it for its current scope; NavLab supplies the
navigation cost of each candidate shape). Orchestrators on top: `skills/app-architecture-steward` (whole-app program:
portfolio → move per feature → ledger → fitness functions) and `skills/rn-native-migration` (RN/native seams, ROI,
hosting patterns, stop rules).

## Quick start (existing codebase)

```bash
node <navlab>/bootstrap.mjs <repo>          # config, adapter, scan, facts, repo profile, skills, AGENTS.md block; no app code
# then in the repo: "install navlab"         -> confirm the detected profile once; probe/signposts/journeys; every skill ready
#                   "review the whole app"   -> app-architecture-steward (orchestrates everything below)
#                   "audit navigation" / "design X afresh" / "should X be native" / "design system audit" / ...
```

**One install, one repo profile.** `navlab.config.json → profile` (+ `navlab.adapter.json → facts`) holds every
codebase convention a skill needs: analytics/flag/storage wrappers, API clients, design-system roots, native DS,
e2e suites, OTA, telemetry, git. `arch/profile.py detect` fills it with evidence; a human confirms; every skill starts
with `arch/profile.py check <skill>` and asks only for what is missing. Never hard-code a convention inside a skill.

## Layout

| path | what |
|---|---|
| `scan/navscan.mjs` | static navigator tree + per-stack cost prediction; adapter learn loop |
| `probe/navprobe.ts` | runtime: live shape, per-action timing, journeys, fetch/axios/redux hooks, signposts |
| `app/modules/lab/ios/` | `LabCore.swift` (os_signpost ruler, hitches, memory, thermal) + Expo module + `LabProbe` view |
| `lab/` | `sweep.py` (traced grids), `analyze.py` (signposts × Time Profiler), `report.py` (aggregate, `--compare`), `model.py` (fit/predict), `journey.py` (real-app journeys), `build.sh` |
| `app/`, `native/`, `flows/` | the synthetic NavLab (RN) and its UIKit twin, plus the simwalk verification flow |
| `baseline/<target>/` | measured results kept with the pack: `model.json`, `report.txt`, per-sweep CSVs, `meta.json` |
| `arch/` | evidence tools for every architecture skill (`arch/README.md`): `facts.mjs` (one AST pass: components, styles, API, navigation, deep links, storage, analytics, flags, native modules, state, effects, waterfalls, testIDs), `profile.py` (the repo profile all skills read), `contracts.py`, `ds.py`, `datalayer.py`, `featuremodel.py` + `intents.py` (model-first redesign), `depgraph.mjs`, `cochange.py`, `replay.py`, `portfolio.py`, `ledger.py`, plus `release.py`, `upgrade.py`, `safetynet.py`, `field.py` |
| `arch/fitness.py` | CI fitness functions as a ratchet: navscan budget, import and single-writer rules, app rules 6–7 (`init` accepts today's violations, `check` fails only on new ones) |
| `learning/gaps.jsonl` | the pack's gap ledger (`arch/gaps.py`): what real repos showed the tools can't do yet, each fix pinned by a test |
| `traces/` | raw `.trace` files (large, local only) |

## The mechanism (why React Navigation gets slow)

Verified in React Navigation 7 source (core 7.24, bottom-tabs 7.21) and measured:

1. **All navigation state is one tree owned by the container.** A nested navigator's state lives inside its parent's
   route. A push in a leaf stack calls the parent's `SceneView.setCurrentState`, which copies the parent's **whole**
   `routes` array, and so on up to the root (`core/src/SceneView.tsx`).
2. **Every navigator on the root→leaf path re-renders on every action.** Measured: navigator renders per push = depth.
   Each render runs `useDescriptors` over **every route in that navigator's state** (`core/src/useDescriptors.tsx`).
3. **Tabs and drawers hold every route in state.** Stacks hold only what is pushed. 300 registered stack screens cost
   nothing; 300 tabs are re-processed on every push below them. The tab bar (`BottomTabBar`, not memoized) maps all
   routes on each render. `BottomTabBarItem` is memoized, so unchanged buttons skip re-rendering, but each still
   costs a label, an a11y string, an href and a memo comparison.
4. **A stack's own route list grows with every push** (native-stack walks it), so deep stacks get slower.
5. **Siblings off the path are skipped** (memoized scenes). Journeys in a root-level stack or modal group take the
   tab tree off the path. (Inferred from source; not yet measured.)
6. **UIKit has none of this.** A push touches only the leaf `UINavigationController`, so native stays flat.

## The model (baseline: iPhone 17 Pro simulator, iOS 26.4, RN 0.88, React Navigation 7, Release)

Per push, fitted by `lab/model.py` on per-config medians, validated leave-one-sweep-out:

```
RN JS CPU     ≈ 23.3 + 1.55·navigators + 0.075·tab_routes + 0.50·stack_len   (ms; held-out error 1–5ms per sweep)
RN render     ≈ 58.9 + 1.35·navigators + 0.083·tab_routes + 0.68·stack_len   (push() → views attached + committed; held-out 2–6ms)
native render ≈ 52.9 + 0.34·navigators + 0.001·tab_routes + 0.12·stack_len   (idiomatic twin, frame-layout grid: `native_frames`)
```
`navigators` = navigators on the root→leaf path. `tab_routes` = routes held by the tab/drawer navigators on that path.
`stack_len` = screens already on the leaf stack.

**Two rulers, never mixed.** The push model covers push() → transition end (the whole interaction). navprobe's
`navaction` interval covers dispatch → navigation state settled, a shorter window. It has its own calibrated arm
(`rn_navaction`, fitted from the `probecal` sweep), and journey.py/navprobe predict in those units. Comparing a probe
measurement with the push model gave a bogus "−15ms unexplained" before this was split.
Simulator coefficients don't transfer: calibrate per target (below).

### What we measured (306 runs, 3 reps, RN and the UIKit twin interleaved)

| knob | RN starts costing at | native |
|---|---|---|
| depth × tab routes | depth 8 × 300 routes: render 185ms (2.8×), JS 132ms/push, push 664ms (+115ms felt), 312MB | flat 68ms |
| nesting alone | depth 8: +13%; depth 12–16: +17–25ms. Depth 3–5 is free | +10% |
| registered stack screens | never (3→300 flat) | flat |
| tabs in one navigator | ~100 (+13%); 300: +24%, +48MB, +160ms startup | flat (UIKit puts tabs past 5 in "More") |
| stack depth (mounted) | ~30–40 deep; at 60: +41% render, +83% JS. `enableFreeze` cuts JS growth to +28% but not wall time or memory | +5% |
| screen size (rows 10→160) | +85ms: the biggest lever | same order |
| common shape: 4 tabs → stack → journey stack | ≈ native (68 vs 69ms at depth 3) | |

Fixed RN costs: about 15ms per push on light screens (Hermes + Fabric + rn-screens), +32MB runtime memory, about 3.3MB per
stacked screen (native about 2.3MB). Startup was equal (about 1.5s on the simulator). Hitches were equal except on deep stacks.

**Native comparison caveat:** the twin's grid first used nested `UIStackView` Auto Layout: 699ms at 480 tiles, 602ms of it
in the solver. With frame layout (what Fabric does with Yoga's output), grid render at 10/40/160 rows is 34/45/92ms against RN's
50/65/135ms. "RN beats native on big screens" was the twin's Auto Layout choice, not iOS. Idiomatic native is about 1.3–1.5×
faster on every screen size. RN's penalty is a fixed per-push overhead plus the navigation multiplier.

## Rules for apps (what to recommend)

| # | rule | applies to | evidence |
|---|---|---|---|
| 1 | Keep tab and drawer navigators small (≤5 tabs; a drawer is a short menu or a plain side panel, not a 30-route navigator) | RN | measured (tabs); drawer inferred |
| 2 | Don't put big tab/drawer navigators above nested navigators: the cost multiplies | RN | measured |
| 3 | Register screens flat in one stack rather than nesting navigators just to group them | RN | measured |
| 4 | Push details and journeys onto a stack above the tabs (root stack / modal group): it takes the tab tree off the path | RN | source; to measure |
| 5 | Cap stack growth: pop a journey as a unit when it finishes, reset a tab's stack on re-tap; watch 30–40 deep | RN (memory: both) | measured |
| 6 | Screen weight first: virtualize lists, no `.map` in a `ScrollView`, fewer views per screen | both | measured |
| 7 | No inline `component={() => ...}`: it remounts the screen on every navigator render | RN | React Navigation docs |
| 8 | Native: avoid nested `UIStackView` Auto Layout on big screens; use frames or compositional layout | iOS | measured |
| 9 | Migrate after fixing structure and content: idiomatic native is ~10–15ms faster per push on light screens and 1.3–1.5× on content-heavy ones, so migrate only hot paths where that remaining gap is user-felt | both | measured |

## Hygiene (each rule broke a real measurement)

1. **Release builds only.** Debug timings are about 3× inflated and wrong in shape.
2. **No accessibility client during measurement.** An attached ax-service (argent or simwalk; it lives an hour) turns
   iOS accessibility on, and then UITableView builds every row (40 instead of 2) and RN builds AX trees. `sweep.py`
   detaches it before every run. On a phone, VoiceOver and argent's device runner must be off.
3. **End intervals natively.** Fabric `onLayout` fires from the shadow tree before the main thread mounts.
   `LabProbe` / navprobe signposts give the real end points.
4. **Quiet host, measured in busy cores:** sweeps wait until fewer than 3 cores are busy (`BUSY_MAX`) and re-measure
   if the host gets busy mid-run. Not the load average: every booted simulator parks 100+ runnable daemons at ~0% CPU,
   so load read 500–800 with half the cores idle (g0053).
5. **xctrace wedges about 1 run in 15** without recording. Detected at 30s/45s, retried 3×, kept in `traces/hung/`.
6. **Interleave the arms**, 3+ reps, medians. The RN/native ratio is the robust number; absolute ms carry host noise.
7. **Never let tooling clobber data:** single-trace analysis prints only; archive superseded sweeps.
8. **Verify UI changes on a simulator** (argent, then a simwalk flow with `until` on every navigating tap) before measuring.
9. **Long device runs heat the phone:** thermal state is logged per run, and `report.py --compare` counts hot runs.

## Calibrating on a new target (device, OS, versions)

```bash
U=<udid>; UDID=$U lab/build.sh && UDID=$U lab/build.sh native     # first device build registers the phone (Personal Team)
python3 lab/sweep.py all --arm both --udid $U                    # ~318 runs -> traces/device/ + report.txt + model.json
python3 lab/report.py --compare traces/sim traces/device
```
Then copy `traces/<tag>/{model.json,report.txt,*/runs.csv,*/intervals.csv}` to `baseline/<target>/` (with `meta.json`).

## navscan adapter (codebase nuances)

`navlab.adapter.json` in the target repo: `roots`, `ignore`, `aliases`, `baseUrl`, `factories` (custom wrappers),
`screenLists` (dynamic list sizes), `screenNavigators` (dynamic mounts), `assumeStackLength`, `unknownListSize`,
`model`. `--learn` writes `learned.*` and a `todo` list. Resolve the todo, re-scan, and confirm the tree matches the app.
Tested against 8 repos (React Navigation v4–v7, Expo and bare, up to 2,065 files / 25 navigators in about 6s).

## Skills in the pack

| layer | skills |
|---|---|
| learning | `arch/gaps.py` (gap ledger: add, rank, close with a test, verify) |
| orchestrators | `app-architecture-steward` (whole app as a program), `rn-native-migration` (RN/native seams, ROI) |
| install | `navlab-install` (the whole pack: profile, adapter, probe, signposts, journeys) |
| specialists | `navlab-audit`, `design-afresh` (model-first), `contracts-scan`, `ds-audit`, `data-layer-audit`, `safety-net`, `field-perf`, `release-boundary`, `upgrade-readiness`, `android-parity` (designed) |
| global (user skills) | `mobile-architecture-lab` (build a new twin lab), `mobile-scaling-lab`, `rn-perf-climb`, `thread-offload` |

**Boundaries come from a model, not from statistics.** design-afresh builds an explicit feature model by reading code
(concepts, one owner per state, responsibilities, flows, every contract accounted for), checked by
`featuremodel.py check`, challenged by a blind reviewer. Git history (cochange/replay) cross-checks it; judging uses
blind semantic replay of real change intents (`intents.py`).

**Judging layouts.** Scope a feature with `depgraph.mjs --own <feature dirs>`. Judge candidate layouts by replay `lift`
(each layout against random layouts with the same module sizes), never by raw modules per commit: fewer, bigger modules
always look better. As-is models record extra `writers` per state; candidates must pass `featuremodel.py check --strict`.
History follows renames, but commits over 40 files (squash merges) are still dropped (g0050).

## Learning loop (gaps)

The pack improves from every real repo it meets. `arch/gaps.py` keeps the ledger in `learning/gaps.jsonl` (pack-side,
never in target repos; repo short name + file:line only, no proprietary code).
1. **Capture before you work around.** When a tool or skill falls short (a code shape it can't read, a wrong or noisy
   answer, a manual step you repeat, a missing tool, skill or input), log it first:
   `python3 $NAVLAB_HOME/arch/gaps.py add --skill <s> --kind <kind> --impact <blocked|wrong-answer|degraded|slow> --repo <r> --what "..." --evidence <file:line> --workaround "..."`.
   `bootstrap.mjs` also harvests candidates (unresolved navscan patterns, missing profile inputs); triage them with `promote` / `drop`.
2. **Rank.** `gaps.py rank`: impact × repos seen in. A gap seen in two repos outranks a worse one seen once.
3. **Fix fixture-first.** Reproduce the gap in a fixture (`scan/fixtures/patterns`, an `arch/*.py --selftest`), watch
   the test fail, fix, watch it pass. Fix the shared function, not the one caller that broke, and check its siblings.
   `gaps.py close <id> --fixed-by ... --test "<command>"`: no test, no close (a fix without one stays `unpinned`, debt).
4. **Never regress.** `gaps.py verify` reruns every pinning test and reopens the gaps whose test fails. Run it before
   publishing the pack and in the steward retro.
5. **Grow.** `missing-tool` / `missing-skill` clusters are proposals for a new tool, lab or skill (`mobile-architecture-lab`
   for an unmeasured cost driver). A lesson that changes how to measure also becomes a hygiene rule above.

## Reporting

Lead with the answer to the question asked. Every number gets its source (measured / predicted, target, reps).
Separate navigation-structure cost (model) from app cost (unexplained). Give fixes ranked by ms saved × frequency.
Say what isn't comparable (pop across arms, simulator vs device, versions).
