# Voyager-1

The layer above Orbit: a job (goal + acceptance scenarios) in, a validated draft PR with evidence out. Orbit gives
agents cited context; Voyager decides whether their change works, with checks the builder cannot see or change.
Named for going further out than Orbit; the next layer out is Voyager-2.

```
job.json ──> workspace ──> scenarios ──> build ──> validate ──> review ──> record ──> evidence ──> pr ──> done
            (worktrees)   (held-out     (agent)   (protected) (diff-only (argent,    (evidence   (draft,
                           tests by a       ^          |        reviewer)  UI jobs)   .json,      idempotent)
                           2nd agent)       └─ retry ──┴── feedback ┘                 PR.md)        └> escalated
```

| File | Job |
|---|---|
| `voyager.py` | CLI + the job state machine (every transition persisted; `job run` resumes after a crash, never duplicates a session, branch or PR); `install` / `status` |
| `validate.py` | the protected validator: scope + protected paths, no weakened tests, fail-to-pass, 3x flake, regression, held-out scenarios, mutation floor |
| `mutate.py` | targeted mutants (stdlib `ast`) on the lines a change touched; survivors = what the tests can't tell apart |
| `strengthen.py` | the strengthening loop: survivors -> a separate agent writes characterization tests -> accepted only if green 3x -> ratchet in `ledger.jsonl` |
| `constraints.py` | what tests pin, legible: per-unit constraint map, and the constraint delta every PR carries |
| `backtest.py` | one-shot vs factory vs best-of-k on the repo's own fix history, judged by the real fixes' tests |
| `env.py` | env fixtures owned by Voyager (a throwaway Postgres seeded with schema + reference tables, no user data) |
| `vy.py` | plumbing: adapters (profiles), sparse worktrees with secrets excluded, the pytest runner, headless sessions |
| `prompts/` | worker, one-shot, scenario writer, reviewer, recorder, selector, strengthener, task describer, leak check |
| `examples/` | starter adapters (`exampleapp.toml`) |
| `tests/` | `voyager selftest`: fixture repo with a real bug + scripted worker; the three proofs in miniature |

**Adapter = `<repo>/.voyager/profile.toml`** (committable, nothing machine-specific): runner, test dir, test file
patterns, protected paths, sparse dirs, test env, env fixtures. Machine-only settings: `~/.voyager/<name>/local.toml`.
Installed by `bin/orbit install --only voyager <repo>` (starter from `examples/`, else detected).

**Isolation.** Jobs never touch the source working tree: a local bare clone (`~/.voyager/<name>/repo.git`) and
sparse worktrees that exclude `.env*`, keys and certs. Workers and tests run with a stripped environment (no
credentials), no MCP servers, and a deny list for push / ssh / psql / docker / curl / gh.

**UI lane.** A job with `--ui` builds the base and the candidate (Release, simulator) in its judge worktree with the
pack's `perf/skills/xcode-build-speed` overlay: one shared compilation cache and SwiftPM clone dir for every job, so a
file is compiled once per machine, not once per worktree. Then it records every scenario with argent on a slim
simulator leased from orbitfleet (`argent-sim full` for a regular one): the base recording must show the problem,
the candidate recording must pass. Universal links don't work on slim sims (swcd is off): drive deep links by scheme.

**Limits (v0).** pytest only (other runners plug in at `vy.pytest` + `constraints.parse_tests`). Mutants: no
equivalent-mutant filter (the floor < 1 absorbs a few). The recorder needs a booted simulator, an installed build and
a way in (login / launch args) for the app.
