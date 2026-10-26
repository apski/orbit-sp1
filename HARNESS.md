# Harness: how long tasks run

A long task is anything that spans sessions or agents: a perf climb, a migration, a design-afresh, a Spectra
conversion of a design system, an Orbit bootstrap, a lab. Each one runs under the seven rules below. The per-skill
table at the end says what each rule means in that skill; each long-task skill repeats its row under `## Harness`.
For a repo with its own Orbit (`agents/orbit/`), `RECONCILE.md` maps the same rules onto the host's mechanisms.

## The rules

1. **One command registry.** Anything an agent needs to do twice is a command, not a one-off script in a session:
   `bin/orbit <cmd>` / `.orbit/orbit <cmd>` (routed by `orbit how`), `.spectra/spectra <cmd>`, `$NAVLAB_HOME/arch/*.py`,
   the perf harness. Every command takes `--json` and exits non-zero on failure. App side: every screen action an
   agent needs gets a deep link or a launch-argument knob, so a flow, a human and an agent all reach it the same way.
2. **Agents drive the running app.** argent for exploring (taps from the AX tree, never from pixels), simwalk flows
   for anything repeated. `testID` / `accessibilityIdentifier` on everything a flow asserts. UI work is done when a
   flow or a screenshot shows it, on a Release build, never on the agent's say-so.
   **argent contaminates later measurements.** Its `launch-app` sets `DYLD_INSERT_LIBRARIES` (its devtools dylib) in
   the simulator's launchd, and `stop-all-simulator-servers` does not clear it: every later launch on that sim,
   `xctrace record --launch` included, loads argent (native launches stalled for minutes; NavLab had to discard runs).
   Before any timing on a sim argent has touched: `xcrun simctl spawn <udid> launchctl unsetenv DYLD_INSERT_LIBRARIES`,
   check with `... launchctl getenv DYLD_INSERT_LIBRARIES` (must print nothing). NavLab `sweep.py detach_tools` does it.
3. **Test first.** Orient → write the acceptance test → implement → verify → record. The acceptance test exists and
   fails before the code: an `xfail` flow, a Spectra screen that fails `verify`, a fixture that reproduces the gap, a
   ledger prediction. The work is done when it flips. Every crash or misjudge fix ships the regression test that would
   have caught it.
4. **Oracle plus corpus.** Judge against a reference you didn't write in this task: the source's own layout (Spectra),
   RN's Yoga (FlexCore), the baseline bench (perf), the measured twin (NavLab), golden fixtures (Orbit). Corpora live
   pinned (commit + sha256), never edited to make a check pass.
5. **Ratchet.** In a React Native repo, rules 3-6 are enforced by the gate (`.orbit/gate`, skill `gate`): Orbit
   check sets as the must-pass list, `gate ratchet` against the base commit, a Stop hook that blocks on red. Each long task has a floor that only rises: a number with the command that reproduces it, labelled
   **measured** (a rerunnable command produced it) or **estimated** (a model or a guess). It rises only with evidence a
   reviewer can rerun, and never by changing the ruler in the same move. Report two numbers, never one blended:
   **breadth** (does it exist: screens captured, surfaces planned) and **ready** (does it hold up: screens passing the
   judge, moves accepted on device).
6. **Handoff.** Sessions end abruptly. Before stopping, and after every accepted step: the gate is green (or the red
   is recorded), and the task's log has an entry (below). The next agent starts from the log plus a green tree, not
   from the chat. Parallel agents: one owner per file (announce with `SendMessage`, check `ListAgents` and mtimes before
   editing), their own simulator (`--udid`), their own build dir (`-derivedDataPath`, `CARGO_TARGET_DIR`), and they edit
   only the files they own.
7. **Priority and escalation.** The next task comes from the gap list (the table's "next" column), stability before
   breadth: fix what fails before adding what's missing. Ask a human only about licences, spending, publishing, personal
   data, destructive git, or anything unclear. When in doubt, leave it out, and write it in the log as a question.

## The log entry

Append to the task's log (the table's "log" column; `DEVLOG.md` in the state dir when the skill has none), newest last:

```
## <date> <agent/session>
did:      <one line per change, file:line or commit>
gate:     <command> -> PASS | FAIL (<what failed>)
floor:    <metric> <before> -> <after> (measured: <command> | estimated: <basis>)   # breadth and ready separately
next:     <the next gap, from the gap list>
ask:      <questions for a human, or none>
```

## Per skill

| Skill | Acceptance (written first) | Gate | Oracle | Floor (ready / breadth) | Log | Next from |
|---|---|---|---|---|---|---|
| `perf-climb`, `rn-perf-climb`, `native-perf-climb` | a hypothesis with evidence + the target metric | `$H accept` (guard, CI verdict) | the accepted baseline, Release | accepted baseline per scenario / scenarios benched | `perf/ledger.jsonl` (`$H report`) | biggest user-felt gap in `$H profile` |
| `thread-offload` | the long task on the waited-on thread, in a profile | same profile + flow rerun, task gone | the before profile | responses moved off-thread with a measured win / tasks found | `DEVLOG.md` beside the perf state | longest task on a response path |
| `mobile-scaling-lab`, `mobile-architecture-lab` | the hypothesis + the knob sweep, before code | both arms' flows `--repeat 3`; hygiene rules | the native twin | knees measured on device / configs swept | the lab's report + `DEVLOG.md` | the widest unmeasured knob |
| `navlab-audit` | the predicted per-push cost (`model.py predict`) | Release journeys under Instruments | NavLab baseline (measured twin) | stacks measured / stacks predicted | `.navlab/` report | stacks furthest above budget |
| `design-afresh` | `featuremodel.py check` on the as-is model; intents listed | `check --strict`, `contracts.py diff` (only intended removals) | blind reviewer + history replay | migration steps shipped / candidates judged | `docs/design-afresh/<f>/README.md` | hotspots first |
| `rn-native-migration` | a ledger prediction per surface | `ledger.py measure`, `contracts.py diff`, journeys on device | measured twin, field data | surfaces migrated within prediction / surfaces ranked | `.navlab/` ledger | stop rules, then next ROI rank |
| `app-architecture-steward` | a ledger prediction per move | the specialist skill's judge; `fitness.py` | `ledger.py score` (calibration) | moves scored / moves planned | `docs/architecture/` program page | evidence table + `gaps.py rank` |
| `field-perf` | the field metric + `--min-n` before wiring | `field.py selftest`, `report --measure` | production percentiles | screens with field data at min-n / screens instrumented | `.navlab/` ledger | screens missing data |
| `spectra`, `spectra-components` | the screen's/variant's IR captured, `verify` failing or unrun | `.spectra/spectra verify` (PARITY n/n within 1pt) | the source's own layout (rn-runtime, jest) | variants/screens at PARITY / captured | `.spectra/DEVLOG.md` | layers bottom-up; "Not done yet" in the skill |
| `orbit-bootstrap` | the `propose --json` before-snapshot | `engine-tests`, `verify fast`, goldens | golden fixtures, labelled router questions | covered files per language (must rise) / files | `docs/orbit/<area>/BOOTSTRAP-<REPO>.md` | ranked proposal items |

NavLab's own learning loop (`$NAVLAB_HOME/AGENTS.md`: `gaps.py add` before a workaround, fixture-first fix, `gaps.py
verify` never regresses) is rule 3 + 7 for the pack itself: a gap is closed only with a pinning test.
