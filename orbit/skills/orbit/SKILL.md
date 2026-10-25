---
name: orbit
description: Orient in this codebase with Orbit, the deterministic index and router (no model, file:line cited). Use before grepping, when starting any code task, when asked "where is X", "what calls X", "what's in this unit", "give me context for this task", "check house rules", or "run fast verification".
---

# Orbit

Orbit is this repo's codebase index. It parses the code into SQLite once, then answers structure questions with
`file:line` citations and no model calls. Ask Orbit first; grep only for what it cannot answer. Needs Python 3.13+.

**This repo's notes come first:** `.orbit/README.md` (which units exist, what's measured, local decisions).
Config: `.orbit/orbit.toml`. Engine docs: `$(.orbit/home)/orbit/SPEC.md` (contract), `.../orbit/README.md`.

```sh
.orbit/orbit index                          # refresh (incremental; first run builds .orbit/.cache/index.db)
.orbit/orbit how "<question>"               # routes a question to the right command
.orbit/orbit status                         # units, languages, sources
.orbit/orbit observe <unit>                 # layers, imports, types
.orbit/orbit refs <Symbol>                  # definitions + references
.orbit/orbit artifacts <unit>               # tests/docs present | missing | stale
.orbit/orbit packet <unit> "<task>"         # budgeted, cited context for one task
.orbit/orbit principles check               # house-rule violations with file:line
.orbit/orbit diagram <unit>                 # layer dependency graph (Mermaid), violating edges red
.orbit/orbit perf <traces>                  # performance trace report (see the repo's [perf] config)
.orbit/orbit verify fast                    # the repo's fast checks (see [check_sets] in orbit.toml)
```

- Every command takes `--json`. A unit is `<kind>:<dirname>`; a bare name works when it is unambiguous.
- Packets list candidate files: treat them as candidates and confirm with `rg` before editing.
- A changed Orbit output needs `UPDATE_GOLDEN=1`, plus one sentence in the commit message saying why.
- Setting Orbit up on a repo, or finding its gaps: the `orbit-bootstrap` skill (`.orbit/census census|propose|report`).
  It proposes changes; a person approves them before anything is applied.
- Product decisions that change proposals live in `.orbit/policy.toml`.
