---
name: voyager
description: "Voyager-1, the layer above Orbit: turn a job (bug, feature, maintenance) into a validated, PR-able piece of work with evidence an independent validator produced, not the builder's say-so. Use when the user says 'voyager', 'run a job', 'make a PR for', 'factory', 'validate this branch', 'strengthen the tests for', 'what do the tests pin', 'constraint map', 'backtest one-shot vs factory', or wants agent work they can trust without re-checking it."
---

# Voyager-1: job -> validated draft PR

Launcher in an installed repo: `.voyager/voyager` (else `$(.orbit/home)/voyager/bin/voyager --repo <repo>`).
Code: `$(.orbit/home)/voyager/` (README.md there). Long tasks follow `$(.orbit/home)/HARNESS.md`.

## Bring it online (once per repo)

1. `orbit install --only voyager <repo>` writes `.voyager/profile.toml` (the adapter: runner, test dir, protected
   paths, sparse dirs, test env, env fixtures) from `examples/<name>.toml` or by detection. Read it with the user:
   protected paths are what no builder may edit (conftest, golden fixtures, pinned corpora).
2. Machine-only settings (e.g. `[env.postgres] seed_from`) go in `~/.voyager/<name>/local.toml`, never the repo.
3. `.voyager/voyager env up`, then `voyager selftest` (fixture repo, scripted worker, 15 golden tests).
4. Online when one real job reaches `done`: `job new --goal "..." --accept "..."` then `job run <id>`.

## Verification levels (pick per job)

`--mode review` (default): one-shot builder + diff-only reviewer, escalate on a flagged risk. `--mode oneshot`:
builder only. `--mode factory`: held-out scenarios (a second reader must confirm a scenario before it can veto),
protected validator, reviewer, retries, UI recordings. Measured 2026-10-25 on 40 fixes from a private production app: factory was not
worth it for small tested bugs (more false rejects, ~3x cost); use it for UI, multi-file or untested areas. Before
making any check a gate, calibrate it: `voyager backtest calibrate` / `calibration`.

## A job

```
.voyager/voyager job new --goal "<symptom + expected behaviour>" --accept "<scenario>" --accept "..." \
    [--pr draft] [--ui '{"app":"<bundle id>","udid":"<sim>"}']
.voyager/voyager job run <id>        # re-run the same command after any crash: it resumes, never duplicates
.voyager/voyager job status
```

- `--accept` lines are the scenarios: the held-out tests are written from them, and the recording walks each one.
- `--ui` is required for anything with a screen: done = an argent screen recording of every scenario passing.
- `--pr draft` pushes the branch and opens a draft PR with the evidence table. Only with the user's go-ahead for that
  repo; the default (`local`) writes PR.md in the job dir.
- Escalated jobs (`job status` shows why) go to the human with the evidence; never re-run them blindly.

## What "done" means (don't report a job done on less)

`~/.voyager/<name>/jobs/<id>/evidence.json`: scope + protected paths, no weakened tests, fail-to-pass on the
builder's tests, 3x no-flake, regression, held-out scenarios, mutation floor, a reviewer that saw only the diff,
and the recording when the job has a UI. Quote the table from PR.md.

## Other commands

- `validate --base A --cand B`: the validator alone on any branch.
- `constraints map <path>` / `constraints delta <base> <cand>`: what tests pin, readable fast (`--json` for agents).
- `strengthen <src file>`: mutants over a unit, a separate agent writes tests for the survivors; the ledger
  (`~/.voyager/<name>/ledger.jsonl`) floor never falls.
- `backtest mine|describe|run --arm A|B|C|judge|score`: one-shot vs factory vs best-of-k on the repo's own
  history, judged by the real fixes' tests. Costs real tokens: get the user's OK and a budget first.
