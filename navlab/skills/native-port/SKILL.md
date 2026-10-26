---
name: native-port
description: "Entry point for moving a React Native app (or parts of it) to native, and for coming back to that work in any state: start or resume the program, see where every surface stands in ~300 tokens, take one surface through readiness -> design -> seam -> build -> measure, and route to design-afresh, rn-native-migration, Spectra, perf and lab skills without re-reading their stores. Use when the user says 'port to native', 'native port', 'move X to Swift', 'resume the native work', 'where are we on native', 'native migration status', 'next native step', 'is X ready to port', or comes back to a migration mid-way."
---

# Native port: the program entry point

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check rn-native-migration`. State: `.navlab/program.json`, written only
through `program.py` (the gate's ratchet watches it). Long tasks follow `$(.orbit/home)/HARNESS.md`.
`P="python3 $NAVLAB_HOME/arch/program.py"`.

## 0. Always start here (context budget)

```bash
$P status                                  # zoom 0: every surface, stage, last measured number, next step, flags (~300 tokens)
$P status --zoom 1 --surface <s>           # one surface: goal, judge, readiness, links with live status, decisions (~1.5k)
$P status --zoom 2 --surface <s> --task "<t>"   # one task: Orbit packet + orbit-rag bundle, budgeted (~3k)
```
Zoom in only as far as the next action needs. Don't open ledgers, job dirs, design docs or traces directly: the
status lines cite them; read one only to act on it. `!!` program flags come first (STOP RULE, > 3 in flight, gate red).

## 1. No program yet: set the goal (once)

1. Run the `ambitious` skill on the user's goal. Keep its rewritten task: a specific hard target and the outside judge.
2. `$P init --goal "<target>" --judge "<the command or measurement that decides it>"`.
3. Candidates: `app-architecture-steward` §1 (portfolio) plus `rn-native-migration` §0–3 (gap × usage, ROI).
   For each surface worth considering: `$P add <surface> --move migrate --entry <navigator/screen file> --unit <orbit unit>`.
   A surface that only needs tuning or redesign gets `--move tune|redesign`; that is often the right answer (AGENTS rule 9).

## 2. One surface, stage by stage

`$P advance <surface> <stage>` refuses until the evidence for that stage resolves (a file or a ledger id; a note never
counts for a required item). After each skill step, write back with `$P set` / `$P link`, then advance.

| to stage | do (skill) | write back | the check |
|---|---|---|---|
| ranked | `rn-native-migration` §1–3: gap, ROI | `set <s> rank=<roi file>` `gap=...` | `rank` resolves |
| ready | `contracts-scan --scope`, `safety-net` (fill gaps with `argent-qa-flows`), Release baseline + `ledger.py add` | `set <s> contracts=... safety=... baseline=ledger:<id>`, plus `state= seam= release= ds= platform= team=` | `$P ready <s>`: items 2, 5, 6 required |
| designed | `design-afresh` (model, candidates, blind judge) | `set <s> design=docs/design-afresh/<f>/README.md` | `design` resolves |
| seamed | `replay.py --map split.json` (crossing per side, co-active), hosting pattern (`rn-native-migration` §4) | `set <s> seam=.navlab/<s>-split.json` | `seam` resolves |
| building | `ledger.py add` FIRST (the acceptance test), then Spectra (`spectra`, `spectra-components`) behind a flag | `link <s> ledger:<id>`; `link <s> voyager:<job>` for jobs | an open prediction is linked |
| measuring | flows `--repeat 3` on Release, `field-perf` once shipped | `link <s> file:<report>` | gate green |
| done | `ledger.py measure <id>`; `contracts.py diff` shows only intended removals; delete the old path | | every linked prediction measured |

Stopping is a result: `$P advance <s> stopped --why "<evidence>"` (stop rules: two missed predictions in a row, or a
boundary tax above forecast). `parked` = valid but not now. Going backwards needs `--why` and shows in the ratchet.

## 3. Experiments and side investigations

A perf climb, twin lab, data-layer audit or Voyager job about one surface is linked, not copied:
`$P link <s> file:<its report>` / `ledger:<id>` / `voyager:<job>` / `gap:<gNNNN>` (a tool gap blocking it). Status reads
each one live, so an experiment started weeks ago shows its current state at zoom 0. A cross-cutting question (does
the design system have native parity, is the platform ready) is its own surface with `--move lab`.

## Rules
- At most 3 surfaces in flight. Finish (or stop) one before starting another.
- Prediction before code: `building` refuses without an open ledger entry.
- Never edit `program.json` by hand, never move a surface back or unlink evidence to look done: the gate ratchet
  flags it against the base, and only a person moves the base.
- Every number in a report comes from `status` (measured) or is labelled estimated.

## Harness (long task)
Acceptance = a ledger prediction per surface; gate = `$P advance` checks + `.orbit/orbit verify gate`; oracle = the
measured twin, journeys on Release, field data; floor = surfaces done within prediction (ready) / surfaces ranked
(breadth); log = `.navlab/program.json` history + the steward's program page; next = zoom 0's first flagged line,
then its in-flight surfaces' `next`.
