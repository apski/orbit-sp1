# Provenance

The engine was extracted on 2026-10-25 from Orbit's first production install, where engine and instance lived together
(engine at commit `075fc83c96`, 2026-10-24). That install now runs this engine from its own `.orbit/` instance.

- **Engine** (here): `orbit/`, `SPEC.md`, `schemas/`, `bootstrap/` (census + catalog), `tests/`, `skills/`, `docs/`.
- **Instance** (per repo, not here): `orbit.toml`, `principles.toml`, `routes.toml`, `policy.toml`, benchmarks, run
  log. Starters are in `examples/`.
- **`orbit perf`** (PerfStack: `orbit/perf.py`, the trace and perf-report schemas, `tests/fixture/perf/`,
  `tests/golden/perf.json`, `docs/PERFSTACK.md`): merged the same day from unmerged work dated 2026-10-18. The A/B arms
  in the fixture are named `legacy` and `next`.
- **Skills** `orbit`, `orbit-bootstrap`: generic; each repo's specifics go in its `.orbit/README.md`.
- To compare with another Orbit checkout: `orbit compare <path>` from the pack root.

Tests: engine 43, census 5.
