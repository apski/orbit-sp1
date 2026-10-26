---
name: rn-native-migration
description: "Plan and run React Native / native coexistence and migration as a measured program: decide per surface (screen, flow, subsystem) whether it stays RN, moves to native, or the other way, using the NavLab model and twin results for the per-push gap, a replay of git history for the ongoing cost of a cross-stack boundary, and ROI; then place seams where real boundaries are, choose the hosting pattern (native shell + RN flows, RN shell + native screens, one RN host vs many), measure the boundary itself, and migrate surface by surface with ledger predictions and stop rules. Use when the user says 'migrate to native', 'move this screen to Swift', 'brownfield', 'add RN to our native app', 'RN and native side by side', 'which screens should be native', or 'migration plan'."
---

# RN ↔ native migration program

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check rn-native-migration` (the repo profile from the install; it asks only for what this skill is missing).

Migration is an investment: it pays per interaction, and it costs a port plus a **permanent dual-stack tax**.
That tax is two design systems, a bridged state, and changes that have to cross the boundary. This skill only
recommends moves whose measured benefit beats that tax. Rules and numbers: `$NAVLAB_HOME/AGENTS.md`.

## 0. Intake: two ways in

| entry | user says | required | then |
|---|---|---|---|
| **discovery** | "which screens should be native?" | the repo (cwd) | portfolio ranks every surface; §1–3 for the top 3–5 |
| **targeted** | "should `src/features/checkout` be native?" | a folder, screen/route name, or entry file | depgraph on that entry; §1–3 for it alone, with 2–3 candidate split lines |

Ask once, in one message, for whatever is missing (defaults in brackets; proceed on defaults if the user says go):
1. **Usage** [none: rank by change pressure and gap only, and say so]: screen views per route, e.g. `{"Checkout": 41000}`
   per month from PostHog/Amplitude/Firebase (the `posthog` skill can pull it), saved as `.navlab/usage.json`.
2. **Release model** [assume OTA for JS]: do they ship JS over the air (EAS Update/CodePush)? If so, every
   cross-boundary change becomes native-first plus a version gate, which multiplies the boundary tax.
3. **Team** [both stacks staffed]: who owns Swift? A native surface with no owner is a liability; say so in the ROI.
4. **Direction** [RN → native]: brownfield the other way (native app adding RN) uses the same steps with the sides swapped.
5. **Value per ms** [rank only]: only if they want ROI in money; otherwise rank by ms saved × usage.

Prerequisites: `node $NAVLAB_HOME/bootstrap.mjs .` (navscan for surfaces) and a git history for §2. No device or
build is needed for the plan; measurement (§4–5) needs Release builds.

## 1. Surfaces and their gap

- Surfaces = the portfolio features (`arch/portfolio.py`), plus non-navigation subsystems (startup, sync, media, editors).
- **The gap** (RN − idiomatic native, per interaction) comes from the best evidence available, in this order:
  1. a measured twin for this surface type (NavLab baseline: about 10–15ms per push on light screens, 1.3–1.5× on content-heavy ones);
  2. the model for its navigation shape (`model.py predict`);
  3. a new twin lab (`mobile-architecture-lab`, references/subsystems.md) when the surface type is unmeasured (lists, editors, media).
- **Fix structure and content first** (navlab-audit, design-afresh, rn-perf-climb). They shrink the gap in both stacks for
  less money. Only the *remaining* gap counts toward migration.

## 2. The boundary tax, from history

A seam is cheap only where little crosses it. Map the candidate split as a design and replay history:
```bash
python3 $NAVLAB_HOME/arch/replay.py . --scope <dep.json> --map split.json   # modules: {"native": [...], "rn": [...]}
```
The share of past commits that would have touched **both** sides is the ongoing coordination cost: each one becomes
two PRs, two releases, a bridge change. Also count the cross-boundary imports, which become bridge APIs. A good seam
has few of both. If none does, the surface isn't separable yet: redesign it first (design-afresh), then migrate.

Also count, per candidate line:
- the contracts crossing it (`contracts-scan` with `--scope`, `[outside]` users), which become bridge APIs;
- the state read on both sides (`data-layer-audit`), which becomes a sync problem;
- the design-system components with no native counterpart (`ds-audit --scope ... --native-ds ...`), which are port work plus dual upkeep;
- the share of changes that need a store release (`release-boundary`), which multiplies every crossing change under OTA.

## 3. ROI per surface

```
benefit/month = remaining gap (ms) × interactions/month (usage) × value per ms (agree it with the team, or rank only)
cost          = port effort + boundary tax (cross-boundary commits/month × coordination cost) + dual design-system upkeep
```
Rank surfaces; put a ledger prediction on each one you choose (`arch/ledger.py add --move migrate --metric <ms/journey> ...`).

## 4. Hosting pattern (measure the boundary itself)

| pattern | fits | measure (mobile-architecture-lab §coexistence) |
|---|---|---|
| native shell, RN flows | mostly native app, RN for fast-iterating flows | first RN entry (runtime start + bundle), each RN root mount, warm vs pre-warmed runtime |
| RN shell, native screens | mostly RN, a few performance-critical native screens | native screen push from an RN stack (react-native-screens hosting), state handoff |
| one RN host vs many | several RN flows inside native | the cost of a host per flow vs a shared runtime and navigator |

Pick by measured boundary cost against usage, not by taste. Keep the navigation shape within NavLab's rules on both sides.

## 5. Run it surface by surface

Each surface is a row in the program (`native-port` skill, `arch/program.py`): the steps below are its stages, and
`program.py advance` checks the evidence for each one. Resume with `program.py status`, not by re-reading this plan.

For each surface:
0. `safety-net` covers its journeys, and `contracts-scan` records the before-inventory.
1. Introduce the seam (interface plus bridge).
2. Build the native or RN version behind a flag.
3. A/B or staged rollout.
4. Measure: journeys on device, plus crash-free rate.
5. Run `ledger.py measure` (journeys in the lab; `field-perf` for production).
6. Delete the old path; `contracts.py diff` must show nothing lost.

Add fitness functions: no new cross-boundary imports outside the bridge, journey budgets, the design-system parity checklist.

**Stop rules:** two consecutive surfaces that miss their ledger prediction, or a boundary tax above forecast →
pause the program and re-plan (`ledger.py score`, revisit §1–3).

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill rn-native-migration --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = a ledger prediction per surface, before the seam; gate = `ledger.py measure`, `contracts.py diff` (nothing lost), journeys on device; oracle = the measured twin and field data; floor = surfaces migrated within prediction (ready) / surfaces ranked (breadth); log = the `.navlab/` ledger plus `.navlab/DEVLOG.md`; next = the stop rules, then the next ROI rank.
