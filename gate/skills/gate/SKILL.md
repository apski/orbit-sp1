---
name: gate
description: "The agent gate for a React Native repo: an LLM-free oracle, a must-pass list and a ratchet that let agents run long tasks (migration, redesign, perf climb) without drifting. Brings it online (orbit.toml check sets, contracts baseline, flows, the Claude Code Stop hook) and runs the climb loop in .orbit/CLIMB.md. Use when the user says 'gate', 'ratchet', 'must-pass', 'oracle', 'let the agent run for hours', 'lock it in', 'stop the agent from loosening tests', 'CLIMB', 'arch gate', or before handing a migration or redesign to a long-running agent."
---

# Gate: oracle + must-pass list + ratchet

Launcher in an installed repo: `.orbit/gate` (else `$(.orbit/home)/gate/bin/gate <cmd> --repo <repo>`).
Code and full help: `$(.orbit/home)/gate/gate.py`. Long tasks follow `$(.orbit/home)/HARNESS.md`; this is its rules 3-6
made mechanical.

| piece | what | command |
|---|---|---|
| oracle | deterministic checks, no model: typecheck, contracts diff, fitness functions, simwalk flows | `.orbit/orbit verify gate`, `... verify gate-device` |
| must-pass list | Orbit `[check_sets] gate` (fast, every Stop) and `gate-device` (simulator) | `.orbit/orbit.toml` |
| ratchet | no ruler looser than the base commit (merge-base with upstream) | `.orbit/gate ratchet` |
| enforcement | Claude Code Stop hook: red blocks the first Stop with the reasons | `.orbit/gate stop` |

## Bring it online (once per repo; a person approves each file)

Prerequisites: Orbit online (`.orbit/orbit.toml` exists), NavLab installed (React Native repo).
1. `.orbit/gate propose`: the checks and Stop hook for this repo. Show it; on approval merge it into `.orbit/orbit.toml`.
2. Contracts baseline: `.orbit/gate contracts --init`, show the inventory (`.navlab/contracts.now.json` kinds and counts),
   and the person commits `.navlab/contracts.baseline.json`.
3. Fitness: if `navlab.fitness.json` is missing, `python3 $(.orbit/home)/navlab/arch/fitness.py init .` (budgets =
   today's values, today's violations accepted) and commit it. design-afresh / steward add import and writer rules later.
4. Flows: `flows/*.mjs` (simwalk format, `~/code/simwalk/FLOWS.md`) for every journey through the surface being changed:
   `safety-net` finds the gaps, `argent-qa-flows` writes them. A known bug gets its own flow with
   `export const xfail = '<repro>'`. Run `GATE_UDID=<claimed sim> .orbit/orbit verify gate-device` until green.
5. Stop hook: add the `.claude/settings.json` block from `propose` (merge with existing hooks). From then on the ratchet
   guards it too.
6. Fill the Score in `.orbit/CLIMB.md` with a command and today's value. `bin/orbit status` shows `gate  online`.

## Running under the gate (agents)

Follow `.orbit/CLIMB.md`: one move, acceptance test first, both check sets green, tighten, commit code + ruler
together, log entry. When the Stop hook blocks you, fix the reasons; if you can't, write the red into the task log with
why, then stop again (the second Stop goes through and the user sees the message). Never edit a ruler to get green:
`gate ratchet` names every loosening, and only a person moves the base (by pushing).

## Limits
- The base is merge-base with upstream; with no upstream it is HEAD, so a local commit resets the ratchet. Run long
  agents on a branch that has an upstream.
- The Stop gate runs on the whole tree, so another agent's red tree blocks you: one worktree per agent (HARNESS rule 6).
- `gate-device` is not run by the hook (minutes on a simulator); the hook only demands a passing run newer than your app
  changes. Spectra screens are not in the device set yet (`spectra verify` takes one IR; no verify-all).
- Fitness budgets are compared as "higher is looser" (every budget is a `max_*`).
