---
name: app-architecture-steward
description: "Steer a whole React Native app's architecture as a program: put every feature on one evidence table (structure, change history, bugs, predicted navigation cost, usage), pick the right move per feature (leave, watch, tune, restructure navigation, redesign afresh, run a new lab, migrate to native), run moves one at a time through the specialist skills (navlab-audit, design-afresh, rn-perf-climb, mobile-architecture-lab, rn-native-migration), record every move as a prediction in a ledger and score it afterwards, and lock wins in with CI fitness functions. Use when the user says 'review the whole app', 'architecture review', 'where should we invest', 'tech debt plan', 'what to fix first', 'quarterly architecture', 'steer the codebase', 'run the program', or wants skills like navlab and design-afresh applied together."
---

# App architecture steward

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check app-architecture-steward` (the repo profile from the install; it asks only for what this skill is missing).

The outcome is **an app that stays fast and easy to change as it grows**, steered by evidence. Specialist skills
answer one question each. This skill decides which question matters, in what order, and whether the answers were right.

Pack: `$NAVLAB_HOME` (rules and baseline in `$NAVLAB_HOME/AGENTS.md`). State lives in the repo:
`.navlab/` (scans, portfolio, ledger) and `docs/architecture/` (decisions, program page).

## Composition map

| question | skill / tool | hands back |
|---|---|---|
| what features exist, how big, how hot, how costly? | `arch/portfolio.py` (navscan + depgraph + git + model) | `.navlab/portfolio.json` |
| is navigation structure the cost? | `navlab-audit` (navscan, navprobe, journeys) | hot paths, predicted vs measured, unexplained |
| what shape should this feature have? | `design-afresh` (depgraph, cochange, replay) | design doc, backtest table, migration steps |
| why is this specific screen slow? | `rn-perf-climb`, `thread-offload` | accepted fixes with a measured ledger |
| the cost driver is unknown or unmeasured | `mobile-architecture-lab` (new twin lab) | knee curves, model |
| should this surface be native? | `rn-native-migration` | ROI per surface, seam plan |
| what must survive any change? | `contracts-scan` (`arch/contracts.py`) | contracts per feature, shared ones, diff |
| one design system, used? what would native need? | `ds-audit` (`arch/ds.py`) | competing DSes, duplicate families, adherence, parity |
| is the data layer the cost? | `data-layer-audit` (`arch/datalayer.py`) | coexisting stacks, refetch, waterfalls |
| is it safe to change? | `safety-net` (`arch/safetynet.py`) | e2e coverage of the journeys a move touches |
| what do real users see? | `field-perf` (`arch/field.py`) | production percentiles per screen; ledger measures |
| what does a release cost? | `release-boundary` (`arch/release.py`) | store vs OTA share, version-skew risk |
| is the platform stable? | `upgrade-readiness` (`arch/upgrade.py`) | dependency and New Architecture risks |
| does it hold on Android? | `android-parity` (designed) | Android model next to iOS |
| did it work? | `arch/ledger.py` | calibration per move type |

## The loop (run quarterly, or when the team asks "what next")

### 1. Triage: one evidence table
```bash
node $NAVLAB_HOME/bootstrap.mjs .                      # once; then fix the navscan adapter (navlab-install §1)
python3 $NAVLAB_HOME/arch/portfolio.py . --since "1 year ago" [--usage usage.json] --json .navlab/portfolio.json
```
- `usage.json` = `{"RouteName": views}` from analytics (PostHog, Amplitude, Firebase). Without it, cost of delay is a guess; say so.
- Sanity-check the feature list with a human: names, ownership, anything missing (non-navigation subsystems such as
  sync, auth or offline belong on the table too; add them as rows by hand with their entry files).

### 2. Decide a move per feature
Start from portfolio's suggestion, then apply judgment and write down **for/against** for each:

| move | when |
|---|---|
| leave | stable, low bugs, no perf issue. *Most features land here; that's healthy.* |
| check-dead | unchanged, unused, or "not reached from entry": confirm and delete |
| tune (`rn-perf-climb`) | one screen slow, structure fine (unexplained cost high in journeys) |
| restructure navigation (`navlab-audit`) | predicted lag ≥ 1 frame from structure; big tab or drawer navigators above deep paths |
| redesign (`design-afresh`) | hotspot × scattered or bug-heavy; change clusters disagree with folders |
| new lab (`mobile-architecture-lab`) | users feel a cost that no model explains (lists, startup, images, ...) |
| migrate (`rn-native-migration`) | the remaining gap after structure and content fixes is RN overhead on a hot path |
| consolidate DS (`ds-audit`) | competing design systems or big duplicate families slow every feature |
| fix the data layer (`data-layer-audit`) | coexisting cache/state stacks, refetch storms, waterfalls on hot screens |
| upgrade (`upgrade-readiness`) | deprecated or legacy-only native deps block the other moves |

Before any restructure, redesign or migration move: `safety-net` covers the journeys it touches, and
`contracts-scan` captures the before-inventory (diffed after the move).

Rank by **cost of delay ÷ effort**: user impact × frequency (usage) × change pressure (commits), divided by an effort
estimate. Keep **≤ 3 moves in flight**.

### 3. Predict before you move (ledger)
Before starting a move, record what it should achieve and how to re-measure it:
```bash
python3 $NAVLAB_HOME/arch/ledger.py add --feature "<f>" --move design-afresh --metric mods_per_commit \
  --baseline 1.33 --predicted 1.10 --lo 1.0 --hi 1.2 --measure "python3 $NAVLAB_HOME/arch/replay.py . --scope ... --map ... | tail -1"
```
Metrics that already have a measuring command: `replay.py` locality, `intents.py score`, `journey.py` ms per action,
`model.py predict`, `field.py report --measure` (production percentiles), `ds.py` adherence, `contracts.py diff` (must be
0 unintended), rn-perf-climb ledger metrics, crash rate or bug-fix share over the next quarter.

### 4. Execute one move at a time
Hand the feature to the specialist skill and follow its judge: perf-climb accept rules, the replay backtest, measured
journeys. Simulator and device work stays in the main agent. Read-only analysis (depgraph/cochange per feature,
scope specs) can fan out to subagents in parallel.

### 5. Measure and score
After the move ships (or after a quarter for history metrics): `ledger.py measure <id>`, then `ledger.py score`.
- Overconfident (hit rate < 50%): widen the next intervals and find which assumption broke. Write it in the decision record.
- Direction wrong: stop that move type until you understand why.
- A move that missed its prediction and didn't help gets reverted or dropped. No sunk-cost defending.

### 6. Lock it in (fitness functions)
Each accepted move adds a CI check so the shape can't drift back. `python3 $NAVLAB_HOME/arch/fitness.py init . [--design candidate.json]`
writes `navlab.fitness.json` (budgets = today's values, today's violations accepted: a ratchet a legacy app can adopt
now); `fitness.py check .` in CI fails only on new violations. It covers:
- **import rules** (eslint `import/no-restricted-paths` / dependency-cruiser) from the design's "must never import";
- **writer rules**: one owner per state (files that write a cache they don't own);
- a **navscan budget**: worst predicted lag per push, routes in a tab/drawer navigator above nested navigators, navigators
  per path, inline `component={() => ...}`, and `.map` inside `<ScrollView>` (AGENTS.md rules 1-3, 6, 7);
- **journey budgets** on the Release simulator for hot paths (`journey.py`), if CI has macOS runners;
- a quarterly `replay.py` re-run for redesigned features (locality must not regress).

### 7. Retro: what held the program back
`python3 $NAVLAB_HOME/arch/gaps.py verify && python3 $NAVLAB_HOME/arch/gaps.py rank`. Triage harvested candidates. Pick
the top 1–2 gaps as pack work for next cycle (fixture first, close with a test), and turn a `missing-tool`/`missing-skill`
cluster into a lab or skill proposal. A move this cycle that stalled on a gap goes in the ledger note.

### 8. Report: the program page
`docs/architecture/program.md` (offer to publish as a page): the portfolio table, the moves with for/against, the
ledger with calibration, what's locked in CI, the gaps that held it back, and next quarter's candidates. Decisions go in
`docs/architecture/decisions/NNNN-<title>.md` (context, evidence, decision, predicted effect, ledger id).

## Rules
- Evidence before opinion: every move cites the portfolio, a scan, a backtest or a trace.
- Don't redesign what doesn't hurt. "Leave" is a decision, and usually the right one.
- Every move has a prediction and an outside judge; the ledger scores the judges too.
- Before presenting the plan, red-team it: a subagent given only the evidence (not your conclusions) argues against
  each move. Keep the moves that survive.
- One move per feature at a time; at most 3 in flight; finish before starting.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill app-architecture-steward --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
