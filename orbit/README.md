# Orbit (engine)

Deterministic codebase engine: parses source once into a SQLite fact index, routes a question to a command, hands an
agent a small cited packet, and verifies. Nothing in it calls a model or the network (except `verify`, which runs the
repo's own checks). Contract: [`SPEC.md`](SPEC.md). Stdlib only, Python >= 3.13 (`glob.translate`, `tomllib`).

This directory is the engine. Each repo keeps its own instance in `<repo>/.orbit/` (config, house rules, routes, policy,
index, run log), installed with `orbit install <repo> --only orbit` from the pack root. Run it as `orbit <command>`
(the pack's `bin/orbit`, any directory inside the repo) or `.orbit/orbit <command>` (the repo's launcher).

```sh
orbit status                      # units, languages, external sources
orbit how "where is UserService defined"   # question -> command + tier + model
orbit observe billing             # layers, imports, types, protocols (file:line cited)
orbit refs UserService            # definitions + references
orbit artifacts billing           # tests/doc: present | missing | stale | derived
orbit principles check            # house rules, violations with file:line
orbit diagram billing             # layer dependency graph (Mermaid), violating edges red
orbit packet billing "fix the retry bug"   # budgeted, cited context for one task (--tier, --budget)
orbit verify fast                 # the repo's checks -> runs/<ts>-<check>.json (exit 1 on required fail)
orbit perf <traces> --html r.html # PerfStack: journey -> screen -> phase attribution, A/B speedups
orbit review                      # weekly: misroutes, no-match, errors, slow -> proposals
```

Every command takes `--json` (schemas in `schemas/`). A unit is `<kind>:<dirname>`; a bare name works when unambiguous.
The index refreshes itself (warm: ~90 ms on a mid-size repo), lives in `.orbit/.cache/` and is safe to delete.

## Layout

| Path | Role (spec layer) |
|---|---|
| `examples/` | Starter instance: `orbit.toml`, `routes.toml` (the router vocabulary), `principles.toml` |
| `orbit/adapters.py`, `sources.py` | Language parse, external sources (L1) |
| `orbit/index.py` | SQLite index, incremental by path + hash (L2) |
| `orbit/observe.py`, `artifacts.py`, `principles.py`, `diagram.py` | Pure analyses over the index (L3) |
| `orbit/perf.py` | PerfStack: L3 over trace evidence (not the index). Spec: [`docs/PERFSTACK.md`](docs/PERFSTACK.md) |
| `orbit/packet.py` | Context packets (L4), with the instance's `principles.toml` and `routes.toml` |
| `orbit/router.py`, `reqlog.py`, `verify.py`, `cli.py` | Cross-cutting: router, request log + review, check adapter, commands |
| `bootstrap/` | Census of a repo vs Orbit's coverage and a capability catalog: what to add (`orbit-bootstrap` skill) |
| `tests/` | Golden fixture repo + acceptance tests; `questions.toml` is the labelled routing set |
| `skills/` | `orbit`, `orbit-bootstrap` (installed into each repo) |

## Tests

```sh
python3 -m unittest discover -s tests -t .        # engine: 43 tests
python3 -m unittest discover -s bootstrap/tests   # census: 5 tests
```

A changed output needs `UPDATE_GOLDEN=1` and a sentence in the commit saying why (spec R12).

## Known limits (what the next person should not trust blindly)

- **Edit tasks pick a unit by file text.** For a packet route with no unit in the question, `router.rank_files` scores
  every unit file (same platform when the task names one) by BM25 of the task's stemmed code words. The packet's Files
  section lists the top 10. Measured on one production app's mobile commits: 41% held-out recall, ~13% precision, so
  read the list as candidates.
- **Regex adapters, no tree-sitter.** Refs are textual: comments are filtered by line prefix and Python docstrings are
  skipped, but string literals are not. `partial` only happens on a decode error. Parity suite deferred.
- **Routing accuracy is a floor, not an estimate.** The labelled questions tuned `routes.toml`, so 100% is in-sample.
  The honest number comes from `orbit review` on real use, after which a person merges word changes.
- **`stale` compares file mtimes.** A fresh clone makes everything equally new; a hint, not a gate.
- **Artifact ladder** matches docs by filename, so `present` means "a file with that name exists".
- **Localization predicate reads one line at a time**: literals on the next line, triple-quoted strings and nested
  interpolations are missed; `Text(verbatim:)` and `#Preview` are skipped on purpose. Limits are in `orbit/adapters.py`.
- **Mechanical tier models must not get DB or numeric investigations** (a small model fabricated a figure once); those
  questions must route to `build`.
- **`verify` exits non-zero only on required `failed`/`error`** (spec 8.4).

## Adding things

- **A principle:** a `[[principle]]` in the instance's `principles.toml` (`id, statement, why, source, status, scope,
  predicate`). Predicates are a closed set (`SPEC.md` 9.1); a new kind is an engine change plus a golden test.
- **A unit kind or language:** config + regex block in the instance's `orbit.toml`; no engine change (R11).
- **A route:** words in `routes.toml`, then a labelled question in `tests/questions.toml`.
- **A check:** `[checks.<id>]` + a set in `[check_sets]`. No `command` means `planned`.
