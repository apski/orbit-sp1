---
name: mobile-architecture-lab
description: "Turn an architecture question about a React Native app ('is X slow in RN?', 'where does it break?', 'should this screen/subsystem be native?', 'RN and native side by side', 'migration plan') into numbers: a calibrated experiment with a native control twin, a fitted cost model validated on held-out data, a codebase scanner plus runtime probe that map a real app onto the model, a keep/restructure/migrate decision with ROI, and a CI gate. Use for RN vs native comparisons, finding performance knees, brownfield/co-existence design, migration decisions, or building a lab for lists, startup, images, state, animations or native modules. The worked example is NavLab (~/code/rn-vs-native) for navigation."
metadata:
  last-updated: 2026-10-24
  example: ~/code/rn-vs-native (NavLab)
  lower-level: "mobile-scaling-lab (sweep mechanics), rn-perf-climb (fix loop), navlab-install / navlab-audit (navigation pack)"
  sibling: "design-afresh (structure - boundaries from code + git history, redesign for current scope, backtest)"
---

# Mobile architecture lab

The aim is to decide architecture with numbers you can defend, not to collect benchmarks. Every lab ends in a
decision (keep, restructure, migrate) with an ROI and an error bar, plus a gate that keeps the decision true.

The target to aim for: **predict the cost of an interaction in an unseen codebase within ±1 frame before running it.**
NavLab got within one frame on held-out sweeps (2–6ms) and predicted its worst config at 173ms against 185ms measured.

## The pipeline (each stage has an outside judge; never let the model grade itself)

| # | stage | output | judge |
|---|---|---|---|
| 1 | **Hypothesis → knobs.** Name the suspected cost drivers ("doses"). Always add *content size* as a knob: it beat every structural knob 10–20× | knob table | the user agrees the knobs cover the question |
| 2 | **Parametric synthetic app + native twin.** One Release build per arm; launch args set the knobs; an autopilot drives it and exits | `app/`, `native/` | flows pass on both arms; the twin is idiomatic (see fairness) |
| 3 | **Shared ruler.** One `os_signpost` core compiled into both arms; intervals end natively (on screen, not JS callbacks) | `LabCore.swift` | A/A runs: the same config twice agrees within the floor |
| 4 | **Traced sweeps with hygiene.** xctrace Time Profiler, arms interleaved, 3+ reps, medians, AX detached, load-gated, hang-detected | `traces/<target>/` | reps agree; anomalies reproduce from the raw trace |
| 5 | **Knee curves + attribution.** Ratio vs baseline per knob; CPU per thread and per cause (rule-priority buckets) | `report.txt` | twin control: does native show the same curve? |
| 6 | **Mechanism from source.** Read the framework source for *why* the curve bends; confirm with a counter (e.g. navigator renders = depth) | mechanism note with file refs | the counter matches the prediction |
| 7 | **Cost model.** Least squares on per-config medians; leave-one-sweep-out validation; error bars on every prediction | `model.json` | held-out error; a second device |
| 8 | **Map real codebases.** Static scanner (AST → structure → model score) with an adapter that learns codebase nuances; a runtime probe for live shape; journeys measured under Instruments | `navscan`/`navprobe`/`journey.py` equivalents | predicted vs measured on journeys in a codebase the model never saw; *unexplained* = app cost |
| 9 | **Decision + gate.** Keep / restructure / migrate per surface with ROI; a CI check on scanner output (predicted lag budget) | decision memo, CI rule | the gate catches a seeded regression |

Package stages 2–9 as a **pack** (AGENTS.md + bootstrap + install/audit skills + baseline results) so the next
codebase is one command away. NavLab is the template: copy its layout, then replace the subsystem-specific parts.

## Red-team rules (each caught a wrong result in NavLab)

- **Reproduce every surprising number from the raw trace before believing it.** Depth 1 slower than depth 2 turned
  out to be a bad end point (Fabric `onLayout` fires before mount).
- **Look for the hidden dose.** 40 table cells instead of 2: an attached accessibility client.
- **Split by sub-variable before concluding.** "Native slower on big screens" was one screen type (Auto Layout grid,
  602ms in the solver). The frame-layout fix cut it 7.5×.
- **Make the twin idiomatic before claiming RN wins.** Then state what remains unfair (UIKit's "More" tab, virtualization).
- **Separate structure cost (model) from app cost (unexplained)** on every real-app measurement.
- **Say what isn't comparable** (end points that differ by framework, simulator vs device, versions).

## Migration / coexistence decision model (corporate finance applied to milliseconds)

For each surface (screen, flow, subsystem):
```
benefit/day = Δms_per_interaction × interactions/day × users      (Δms from the model or a twin measurement)
cost        = port effort + ongoing dual-stack cost (bridging, two design systems, two test suites)
decide: restructure if the structural fix (model) recovers most of Δms; migrate only if the remaining Δms is RN fixed
overhead on a hot path; keep otherwise.
```
- **The structural fix almost always wins.** It fixes every screen on the path and costs no second stack. In NavLab,
  RN's penalty vs idiomatic native is ~10–15ms per push on light screens and 1.3–1.5× on content-heavy ones
  (grid 135 vs 92ms at 480 tiles). Structure and content fixes come first because they help both stacks.
  root view), native screen in an RN stack, shared state across the bridge, startup of the RN runtime on first entry.
  Each boundary is a lab with its own knobs (references/subsystems.md §coexistence).

## Choosing the next lab

Pick the subsystem with the largest *user-felt* unexplained cost in the audit of the real app. Designs for the
next ones (knobs, ruler, twin, mechanism to read, scanner and probe ideas) are in `references/subsystems.md`:
lists, startup, images, state management, animations/gestures, native modules/JSI, networking, brownfield coexistence.

## Output of a lab (what the user gets)

1. The answer to the hypothesis in one sentence, with the knee.
2. One table per knob: RN / native / ratio, with the knee marked; the mechanism with source references.
3. The model with held-out error; a predictor command.
4. The real-app audit: hot paths, predicted vs measured, unexplained.
5. Decisions: keep / restructure / migrate, with ROI and confidence; the CI gate.
6. Caveats and the next experiment that would most reduce uncertainty (usually a device run, or the measured twin of an inferred claim).
