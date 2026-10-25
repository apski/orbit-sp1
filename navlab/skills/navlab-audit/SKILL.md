---
name: navlab-audit
description: "Audit a React Native app's navigation performance with NavLab: scan the navigator tree, predict per-push cost for every stack from the measured baseline model, confirm the live shape with navprobe, measure real journeys under Instruments, separate navigation-structure cost from app cost, and compare against the RN-vs-native baseline. Use when the user says 'why is navigation slow', 'audit navigation', 'scan for hotspots', 'nav performance', 'compare with the baseline', 'which flows are at risk', 'is our navigator structure a problem', or 'what should we migrate to native'."
---

# NavLab audit

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check navlab-audit` (the repo profile from the install; it asks only for what this skill is missing).

Requires the install (`navlab-install`). Rules, mechanism and numbers: `$NAVLAB_HOME/AGENTS.md`.

## 1. Static: where can it be slow?

```bash
node $NAVLAB_HOME/scan/navscan.mjs . --top 20 --json .navlab/navscan.json
```
- Read the tree first. Fix the adapter (navlab-install §1) before trusting any number.
- **Hot paths:** stacks ranked by predicted lag vs native.
- **Flags:** big tab/drawer navigators above nested navigators (the multiplier), deep paths, inline `component={() => ...}`, unreachable navigators.
- Predictions assume a leaf stack length (`--stack`, default 5). Section 2 replaces that with real lengths.

## 2. Runtime shape: what do users actually build up?

Run the app (Debug is fine for shape) through the real flows (argent or a human), then read the live shape:
`globalThis.__NAVPROBE__.summary()` via argent `debugger-evaluate`. Look at `max.stackLen`, `max.totalRoutes`
and the actions with their shapes. Re-score the worst paths with the real lengths:
`python3 $NAVLAB_HOME/lab/model.py predict --model <model> --path tabs:5,stack,stack --stack <real max>`.

## 3. Measure: Release journeys under Instruments

```bash
python3 $NAVLAB_HOME/lab/journey.py <journey> --reps 3          # simulator or --udid <phone>
```
Per action you get: measured ms, JS CPU, predicted JS for its shape, **unexplained** (measured − predicted),
main-thread CPU, hitches and CPU buckets.
- Small unexplained: the cost is navigation structure. Fix the structure (AGENTS.md rules).
- Large unexplained: the screen itself (content size, re-renders, network, JS work). Drill in with the
  buckets, argent React profiler (component commits) and `argent-native-profiler`, then hand off to `rn-perf-climb`.
- Hygiene matters: Release only, no accessibility client attached, a quiet host, 3+ reps (AGENTS.md §hygiene).

## 4. Compare with the baseline

- The baseline (`navlab.config.json → baseline`) comes from the synthetic NavLab app: the same RN/navigation
  stack measured against a UIKit twin. It tells you what navigation structure alone costs, and what native would cost.
- **On a different device or OS**, calibrate first: run the NavLab suite on that target
  (`python3 $NAVLAB_HOME/lab/sweep.py all --arm both --udid <id>`), which writes its own `model.json`, and use
  that model. Compare the two targets with `report.py --compare`.
- When versions differ (RN, React Navigation), say so. The coefficients are version-specific.

## 5. Report

Lead with the answer. Then give:
1. A table of the top paths: predicted vs measured, unexplained, and the cause.
2. Flags, with file:line.
3. Fixes ranked by ms saved per push × how often users hit that path. Use the AGENTS.md rules: small tab bars,
   flatten, cap stacks, root-level journeys, and screen weight first.
4. **Migration candidates.** A screen is worth porting to native only if its measured cost is mostly RN fixed
   overhead on a hot path: about 10–15ms per push on light screens, 1.3–1.5× on heavy ones, measured against idiomatic native.
   Fix structure and content first; they help both stacks.
5. Caveats: simulator vs device, versions, anything the adapter left unresolved.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill navlab-audit --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = the predicted per-push cost per stack (`model.py predict`) before measuring; gate = Release journeys under Instruments; oracle = the NavLab baseline (measured twin); floor = stacks measured (ready) / stacks predicted (breadth); log = the `.navlab/` report plus `.navlab/DEVLOG.md`; next = stacks furthest above budget, then `gaps.py rank`.
