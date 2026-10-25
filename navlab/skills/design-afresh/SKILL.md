---
name: design-afresh
description: "Reimagine a feature as if written new today, keeping its full existing scope: build an explicit, checked model of what the feature is (concepts, state ownership, responsibilities, flows, contracts) by reading the code, derive boundaries from that model, cross-check with git history, design 2-3 clean shapes, judge them with blind reviewers replaying real change intents (and NavLab perf where navigation is involved), then plan a shippable strangler migration with fitness functions. Use when the user says 'design this afresh', 'how would we build this from scratch', 'if we wrote this today', 'find the boundaries', 'this feature grew organically', 'reimagine the architecture', 'clean up this module', 'what shape should this have', or before a big refactor or rewrite."
---

# Design afresh

Code that grew over years carries its history in its shape. The question: **with the scope this feature has today,
what shape would a strong team give it now, and how do we get there safely?** Boundaries come from an explicit,
checkable **model of the feature** built by reading the code, not from folder names and not from commit statistics.
History and metrics cross-check the model; blind reviewers judge the candidates.

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check design-afresh` (repo profile: aliases, facts conventions).
Tools: `$NAVLAB_HOME/arch/` (`facts.mjs`, `depgraph.mjs`, `featuremodel.py`, `contracts.py`, `cochange.py`, `intents.py`, `replay.py`).

## 0. Should it be redesigned at all?
Cheap signals first: `portfolio.py` row or `cochange.py` hotspots. If it rarely changes, has few bug fixes and no perf
issue, say **leave it** and stop.

## 1. Locate and extract facts
```bash
node $NAVLAB_HOME/arch/facts.mjs .                                   # .navlab/facts.json (whole app, once)
node $NAVLAB_HOME/arch/depgraph.mjs . --entry <navigator/screen file> [--entry ...] --own <feature dir> [--own ...] --json .navlab/<f>-dep.json
python3 $NAVLAB_HOME/arch/contracts.py .navlab/facts.json --scope .navlab/<f>-dep.json     # what must survive
python3 $NAVLAB_HOME/arch/featuremodel.py scaffold --facts .navlab/facts.json --scope .navlab/<f>-dep.json --name <f> --out docs/design-afresh/<f>/model.json
```
Add screens registered elsewhere (root modals, deep-link hosts) as extra `--entry`. Pass the feature's own folders as
`--own` (its state, queries, components): without it, a store that a badge elsewhere reads drops out of scope as "shared".

## 2. Build the model (read the code; every claim cites file:line)
Fill `model.json`:
- **concepts:** the domain nouns, as users and the code name them.
- **state:** every piece of state with **exactly one owner** (a unit, `server:<op>`, `external:<x>` or `platform:<x>`) and its readers.
  In the as-is model, list every other unit that also writes it under `writers` (a warning; it is usually the finding).
- **responsibilities:** what the feature does, each mapped to units.
- **flows:** user journeys as ordered responsibilities.
- **dropped:** accidental growth deliberately left out (dead flags, duplicated components, workarounds whose reason is gone), each with why.

Then `python3 $NAVLAB_HOME/arch/featuremodel.py check docs/design-afresh/<f>/model.json --repo .` until it PASSES.
It refuses any of these:
- a unit the model doesn't explain;
- state with zero or several owners;
- unknown references;
- cites to lines that don't exist;
- **any contract (API op, storage key, route, event, flag, deep link) the model never accounts for.**

## 3. Challenge the model (learning loop)
Spawn a reviewer subagent with only the code paths and the model. Its job is to find claims the code contradicts and
code the model doesn't explain. Fix the model, re-run `check`, and log each correction in `model.json → notes`. Then
confirm the concepts with the user ("is a Watch the same thing as an Alert condition?"). Domain language is theirs.

## 4. Cross-check with history (it checks the model, it doesn't make it)
`python3 $NAVLAB_HOME/arch/cochange.py . --scope .navlab/<f>-dep.json` tells you three things:
- **hidden coupling** the model must explain, e.g. a backend schema changing with the form, or a sibling feature with the same concept;
- **hotspots** (where complexity hurts);
- **clusters that disagree with the model** (a question to investigate, not a verdict).

## 5. Boundaries, then 2–3 candidates
Derive boundaries from the model:
- group responsibilities by concept;
- give each piece of state one writer inside one boundary;
- minimize flow steps that cross boundaries;
- extract concepts that appear twice (shared modules).

For each candidate, give:
- modules with responsibilities and public interfaces (sketch real types), plus a target file tree;
- data flow and state ownership;
- **navigation shape** scored with NavLab (`python3 $NAVLAB_HOME/lab/model.py predict --path ... --stack N`; AGENTS.md rules);
- what it deletes (`dropped`), and its public surface.

Write each candidate as its own model (same concepts, new units/modules), so it can be checked (`check --strict`: one
writer per state) and replayed. Make
the candidates genuinely different: by concept vs by journey, shared component vs copy, server state vs a client
state machine, an RN vs native seam for one hot surface (`rn-native-migration` decides).

## 6. Judge (never grade your own design)
1. **Semantic replay** (primary):
   - `python3 $NAVLAB_HOME/arch/intents.py list . --scope .navlab/<f>-dep.json --json .navlab/<f>-intents.json`, plus roadmap items via `--issues`.
   - A reviewer subagent that has **not** seen your rationale gets each candidate model and the intents, and answers per intent: modules touched and contracts moved.
   - Save its answers as `{"candidates": {"A": {"<intent>": [modules]}}}`, then run `intents.py score answers.json`. Fewer modules per intent wins.
2. **File replay** (secondary): `replay.py` with each candidate's file → module map. It's mechanical but biased to the old shape,
   and fewer, bigger modules always score better: read `lift` (vs random layouts with the same module sizes), not the raw count.
3. **Contracts:** `featuremodel.py check` on each candidate model, so no contract is lost.
4. **Perf:** the NavLab prediction for the navigation shape; measured journeys if the feature is on a hot path.

Pick one, with for/against. If the current shape wins, say so: "keep it, delete the dropped parts".

## 7. Migration: strangler, not big bang
1. Seam first.
2. Move one responsibility or cluster at a time (hotspots first).
3. Delete the dropped parts.
4. Flip consumers.
5. Remove the old paths.

Each step ships on its own and can be reverted. Before starting, the `safety-net` skill covers the touched journeys.
Afterwards, `contracts.py diff` between before and after must show only intended removals.

Fitness functions that keep the shape (`arch/fitness.py`: put them under the candidate's `fitness` key, then
`fitness.py init . --design candidate.json` ratchets today's violations):
- import rules from "must never import", and writer rules (one owner per state);
- a navscan budget;
- `featuremodel.py check` in CI against the target model;
- `intents.py` re-scored quarterly.

## 8. Deliverable
`docs/design-afresh/<feature>/` with `model.json` (as-is, checked), `candidate-*.json` and `README.md`, in this order:
1. the answer;
2. the as-is model summary;
3. the scope (contracts);
4. boundaries with evidence;
5. the candidates;
6. the judge tables;
7. the decision;
8. the migration steps;
9. the fitness functions.

Use mermaid for as-is vs target. Record the expected effect as a ledger prediction (`arch/ledger.py add`).

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill design-afresh --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = `featuremodel.py check` passing on the as-is model and the intents listed, before any candidate; gate = `check --strict` + `contracts.py diff` (only intended removals); oracle = the blind reviewer and history replay, never your own grading; floor = migration steps shipped (ready) / candidates judged (breadth); log = `docs/design-afresh/<f>/README.md` plus a `DEVLOG.md` beside it; next = hotspots first.
