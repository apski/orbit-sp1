# Orbit: generic specification

Status: draft v1. The target contract for any codebase. The reference implementation is this engine (see `README.md`); sections marked **(impl)** describe it, the rest is the contract.

## 1. What Orbit is

Orbit is a deterministic engine that answers questions about a codebase and hands agents small, sourced context for a change. It has three jobs, in order:

1. **Understand without a model.** Parse source once into a fact index. Same input, same output.
2. **Give each agent the minimum context.** Route a question to a command, return a budgeted packet of facts and rules, not whole files.
3. **Verify and learn.** Run checks against stated rules, keep or revert changes on evidence, and turn repeated failures into rules.

Orbit is not an agent. Nothing in the core calls a model. Models read Orbit's output; Orbit never reads a model's output as fact.

## 2. Core rules

| # | Rule | Why |
|---|---|---|
| R1 | The core knows only **units, layers, facts, rules, checks, trials**. Languages, platforms and external tools live in config and adapters. | One engine, many codebases. |
| R2 | Each source file is parsed **once per revision**. Everything reads facts from the index, never from files directly. | Speed (~30 ms warm) and a single source of truth. |
| R3 | Outputs are **sorted, versioned and content-hashed**. Same tree plus same config gives byte-identical output. | Determinism is testable and diffs are meaningful. |
| R4 | A fact lives at the **lowest layer that can hold it** (source, structure, spec, codified, evidence). Derived things are never hand-edited. | Prevents spec prose from restating what the parser knows. |
| R5 | Every derived artifact records its **inputs** (file hashes, config hash, tool version). A stale input reports drift. | Trust: you can tell when an answer is out of date. |
| R6 | Commands **do not call each other in-process**. Composition is via files or subprocess. | Keeps each command testable and replaceable. |
| R7 | Only the **verify** and **run** commands start external processes. The core never calls a network service or a model. | Reads stay fast, offline and reproducible. |
| R8 | Every rule is either a **checkable predicate** or it is labelled as prose and not gated. | Stops "principles" from being decoration. |
| R9 | Every request is **logged** (command, args, outcome, duration). Misroutes feed a review. | Makes the router improvable. |
| R10 | Every packet fact carries a **source reference** (file:line or artifact id) and a revision. | Agents can cite and re-check. |
| R11 | Config is **data**, adapters are **code behind a fixed interface**. A new codebase needs config and adapters, never a core change. | Portability. |
| R12 | Changes to Orbit's own logic are gated by **golden tests** (fixture repo in, expected JSON out). | Determinism regressions are caught. |

## 3. Layers

```
Agents     | packets · swarm waves · tier per task              |  consume
Context    | spec (human, cites structure)                      |  L4 context
           | rules (principles with predicates)                 |
           | evidence (check results, renders, runs)            |
Analyses   | rule evaluation · UI usage                         |  L3 derived
           | artifacts (representation ladder) · plan           |
Index      | files · units · facts · refs · listing             |  L2 structure
           | (SQLite, incremental, one parse per rev)           |
Adapters   | language (parse) · platform (units, layers)        |  L1 adapters
           | external sources (IR, Figma, design tokens)        |
Source     | repo files · config (orbit.toml)                   |  L0
```

Cross-cutting: **router** (question to command), **request log**, **verify** (checks, result schema), **trials** (A/B on isolated copies), **ledger** (kept or reverted rounds), **learning** (experience to lessons, reviewed by a person).

### 3.1 Layer responsibilities

| Layer | Owns | Must not |
|---|---|---|
| L0 Source | Code and config files. Truth for what exists. | Be written by Orbit. |
| L1 Adapters | Parsing (per language), unit/layer rules (per platform), external reads (per source). Each returns facts in the common schema. | Hold business rules. Decide what a fact means. |
| L2 Index | Files, units, facts, refs, directory listing. Incremental. | Answer questions. It stores and looks up. |
| L3 Analyses | Pure functions over the index: rule evaluation, UI usage, artifact status, plan generation. | Parse files directly. Call each other in a cycle. |
| L4 Context | Spec text, rules, evidence, packets. Human-written parts live here. | Restate derived facts. Cite them. |
| Agents | Consume packets, edit code, produce evidence. | Be trusted as the source of structure. |

## 4. Adapters

Adapters are the only code that knows a language or an external system. Each has a fixed interface and a golden test.

### 4.1 Language adapter (parse)

Input: one file (path, bytes). Output: facts of the common kinds (section 5).

Minimum per language:

- `ext` list
- `import` extraction (module name per import statement)
- `type` declarations (kind, name, line, visibility)
- `ref` extraction (identifiers used, with line); optional but needed for `refs`
- `ui_usage` (optional; for UI codebases: component usage with a kit pattern)
- `ui_literal` (optional; a user-visible string literal passed to a text API without the localization wrapper)

**(impl)** Rust: `use a::b::{C, D};` is an import of `a::b`, `mod x;` an import of `x` (structural: the file owns module `x`). `struct`, `enum`, `trait`, `type`, `union` are types; `impl X` is a type fact of kind `impl` named `X`, and `impl Trait for X` gives it base `Trait`, so `protocol` facts answer "who implements Trait". Functions are not types (as for Python `def`).

Parser choice: tree-sitter grammar where available, regex fallback. When the tree has errors, merge regex results and mark the file `partial`. The golden parity suite runs both parsers and records the diff.

### 4.2 Platform adapter (units and layers)

Defines what a **unit** is and what its **layers** are.

- `units`: glob patterns; each match is one unit (a feature, a module, a package).
- `layers`: first-level directories inside a unit (e.g. `Domain`, `Data`, `Presentation`). Layer names are config, not code.
- `layer_root` (optional, per unit glob): a directory inside the unit whose first-level directories are the layers instead, e.g. `"src"` for a Cargo crate (`src/lots/` is layer `lots`, files directly in `src/` are the root layer). Directories beside it (`tests/`, `benches/`, `examples/`) stay indexed as layers of their own name; a `src/tests/` module would share the `tests` layer.
- `exclude_units`: globs that match but are not units (shared code).
- `kit_patterns`: regex for design-system or shared-library symbols (e.g. `\bHF[A-Z]\w+`).

### 4.3 External source adapter (read-only)

Each external source returns structure, never edits it. Examples: RN flow map JSON, route graph, state machine, component library, design-token export, backend endpoint list, feature-flag verdicts.

Contract:

- `read(config) -> records[]` with `id`, `kind`, `source_path`, `hash`
- Missing source gives `unavailable` status, never a crash and never an empty result that looks like success.

### 4.4 Check adapter (verify)

Runs a command, captures exit code, duration, log path, evidence, artifacts. Output conforms to the check-result schema (section 9). Only the verify command invokes these.

## 5. Index schema

Single SQLite file. Rebuilt incrementally by path + content hash.

| Table | Columns (minimum) | Notes |
|---|---|---|
| `files` | path PK, hash, size, lang, parse_status (`ok` / `partial` / `skipped`), mtime | Revision key for everything else. |
| `units` | id PK, glob, root_path | One row per unit. |
| `facts` | id PK, file, line, kind, name, unit, layer, data JSON | Kinds: `import`, `type`, `ref`, `ui_usage`, `kit_symbol`, `shape` (UI structure), `protocol`, `ui_literal`. |
| `refs` | symbol, file, line, unit | Derived from `ref` facts; supports `orbit refs <Symbol>`. |
| `listing` | path, kind, unit, layer | Cheap directory queries without re-walking the tree. |
| `meta` | key, value | Schema version, config hash, adapter versions, index build time. |

Invariants:

- A fact row is deleted and re-inserted when its file hash changes. No partial files.
- `schema_version` mismatch forces a full rebuild, never a silent migration.
- Index is a cache. Deleting it must only cost time.

## 6. Config (`orbit.toml`)

One file, data only. Sections:

| Section | Holds |
|---|---|
| `[project]` | name, root (relative), paths to features, rules, runs |
| `[units]` / `[platform]` | unit globs, excluded units, layer names, kit patterns |
| `[languages.<name>]` | ext, import regex or grammar, type regex or grammar |
| `[index]` | DB path, include globs, ignore rules (git-tracked + untracked) |
| `[analysis.<name>]` | per-analysis settings (UI sources, excludes, thresholds) |
| `[sources.<name>]` | external source paths and templates (`{feature}` placeholders) |
| `[budget]` | default token budget per packet, per-tier limits |
| `[models]` | tier to model id (mechanical, build, explain) |

Rule: the config hash is recorded in every derived output (R5).

## 7. Context architecture

Five layers, each with a different owner and a different job. A fact lives at the lowest one that can hold it.

| Layer | Form | Owner | Regenerated? | Example |
|---|---|---|---|---|
| source | code | engineers | n/a | `Domain/Reducer.swift` |
| structure | JSON from index and analyses | Orbit | yes, never hand-edited | unit facts, UI-usage counts, flow graph |
| spec | markdown, short, cites structure ids | engineers | no | "Checkout's Domain holds pricing rules; see structure `checkout.domain.facts`" |
| codified | principles with predicates, tests, fixtures | engineers | no | `NF-2: Domain imports only Foundation` |
| evidence | check results, screenshots, run logs, trial scorecards | Orbit and agents | yes, per run | `runs/2026-10-13-sentinel.json` |

Rules:

- **Derivable to structure.** Do not write in a spec what the index can compute.
- **Checkable to codified.** A spec sentence that can be a predicate must become one.
- **Prose only** for intent and trade-offs neither layer can hold. Each prose claim names the decision id that owns it.
- Specs cite structure by id; they never copy counts or lists. Copied counts go stale; citations do not.

### 7.1 Artifact ladder

Every feature (unit) has a set of expected artifacts, each at a layer, each with a `level` (the first gate that requires it). Status per feature: `present`, `missing`, `stale` (newest artifact older than newest source file), or `derived` (always computable).

Config: one row per artifact kind with a path template, a kind (`file`, `derived`, `manifest`, `ir`), a `level`, and `stale_if_source_newer`.

### 7.2 Packets

A packet is the context handed to one agent for one task.

Required fields (schema `packet.schema.json`): `feature`, `task`, `git` (base commit, dirty files), `budget`, `tokens`, `over_budget`, `dropped`, `sections[]` (title, tokens, mandatory), `markdown`.

Build rules:

1. Mandatory sections first: the contract (spec section the task touches), the invariants that apply, the verify commands.
2. Then the facts the task needs (from the index, filtered by unit and layer).
3. Then the house rules that match the unit (predicate text plus the status).
4. Stop at budget. Record `dropped` so the agent knows what it did not get.
5. Every line cites its source (file:line or artifact id). No raw file dumps unless a section asks for a file by name.

Size target: 0.3-2k tokens per agent task; up to ~6k for a whole-feature plan.

### 7.3 Tiers

Each task is tagged with a model tier by the router, not by the agent:

- `mechanical`: lookups, listings, formatting. Small model.
- `build`: edits against a packet. Mid model.
- `explain`: plans, judgment, rule changes. Largest model.
- none: pure reads that need no model.

Escalation: a failed check moves the task up one tier, once. Two failures go to a person.

### 7.4 Knowledge

Two stores, both data files reviewed by a person:

- `experience.jsonl`: one line per observed failure or surprise (command, input, outcome, classification).
- `lessons.toml`: distilled rules with a `check` (how to verify) and a `confidence`. A lesson is promoted to a principle only when its check is a predicate.

Failure classes come from `failure-patterns.toml` (regex or predicate to class). Repeated unclassified failures become proposals, never automatic edits.

## 8. Flows

### 8.1 Index flow (every command that reads code)

```
files (include globs, git ls-files + untracked, ignores applied)
  -> hash each file
  -> changed? -> adapter.parse(file) -> facts
  -> replace facts for that file in one transaction
  -> meta.updated_at
```

Warm run: hash only. Cold run: full parse.

### 8.2 Question flow (router)

```
user question
  -> router: score routes by words (deterministic, no model)
  -> top route -> command + tier + packet template
  -> reqlog.start
  -> command runs against index / analyses
  -> emit (text or --json), reqlog.finish (outcome, duration)
  -> if no route scores above threshold: log as no-match
```

`review` reads the request log weekly: misroutes, no-match questions, errors, slow commands, then proposes fixes to routes or commands.

### 8.3 Context flow (task)

```
task text + feature id
  -> artifacts(feature): which layers exist and which are stale
  -> if a required layer is missing: report it; do not invent it
  -> packet(feature, task): sections in order, budget, citations
  -> agent (tier from router) edits code using the packet
  -> verify(feature): run checks (adapter), write result (schema)
  -> keep, or revert and log the attempt
```

### 8.4 Verify flow

```
verify <check-set>
  -> for each check: run adapter command with timeout
  -> status in {passed, failed, error, unavailable, planned}
  -> write runs/<timestamp>-<check>.json (schema) and log file
  -> exit code: non-zero if any required check failed or errored
```

`unavailable` means the tool is missing. It is never reported as `passed`.

### 8.5 Trial flow (A/B)

```
define trial: two architectures or two context policies, one task set, hidden graders
  -> copy the repo to an isolated worktree per arm
  -> run the same packet and checks in each arm
  -> score with hidden graders, store scorecard (schema)
  -> ledger row: arm, score, cost, decision (keep/revert)
```

Report n, noise and error counts with every number. A single run is an anecdote.

### 8.6 Learning flow

```
experience.jsonl (per failure)
  -> classify (failure-patterns.toml)
  -> repeated class without a lesson -> proposal
  -> person accepts -> lessons.toml (with check)
  -> check becomes a predicate -> principle (status observed, then enforced)
```

Principle status ladder: `observed` (reported, never blocks) to `enforced` (verify fails on violation). Promotion needs a measured zero-violation run and a person's sign-off.

## 9. Schemas

Every machine-readable output has a JSON schema under `schemas/` and a `version` field.

| Schema | Used by | Required fields |
|---|---|---|
| `observation` | `observe` | unit, layers, imports, types, protocols |
| `packet` | `packet` | feature, task, git, budget, tokens, sections, markdown |
| `check-result` | `verify` | check, status (passed/failed/error/unavailable/planned) |
| `principle` | `principles` | id, statement, why, source, status, scope, predicate |
| `trial` | trials | arms, tasks, graders, scores, n |

Principle predicates are a small **closed** set: `layers_present`, `forbid_import`, `forbid_symbol`, `forbid_ui_literal`, `max_loc`, `single_conformer`. Section 9.1 pins each one. Adding a predicate kind is a core change and needs a golden test.

### 9.1 Predicate definitions

All predicates apply to the units matched by the principle's `scope` and read only the index (R2). A violation is `{principle, file, line, detail}`; a unit-level violation anchors at line 1 of the unit root.

| Predicate | Args | Violation when |
|---|---|---|
| `layers_present` | `layers` | a unit in scope lacks one of the named layer directories |
| `forbid_import` | `pattern`, optional `layers` | an `import` fact (in the named layers, if given) has a module matching the regex |
| `forbid_symbol` | `pattern`, optional `layers` | a `ref` fact (in the named layers, if given) has a symbol matching the regex |
| `forbid_ui_literal` | `pattern`, optional `layers` | a `ui_literal` fact (in the named layers, if given) has text matching the regex (`.` = any) |
| `max_loc` | `max`, optional `layers` | a file (in the named layers, if given) has more lines than `max` |
| `single_conformer` | `protocol`, optional `max` (default 1) | more than `max` `type` facts in one unit declare the protocol/base matching the regex |

## 10. Determinism and testing

- **Golden tests.** A fixture repo (small, checked in) with expected JSON for every command. Changing output requires updating the golden and explaining why.
- **Parser parity.** Run tree-sitter and regex on the same corpus; the diff is a tracked number, not a failure.
- **Ordering.** Every list is sorted by a stable key before output.
- **Clock and env.** No timestamps in derived data except `meta`. No env-dependent paths in output.
- **No network, no model** in any command except `verify` and `build --run`.
- **Budget test.** A packet never exceeds its budget without setting `over_budget`.

## 11. Bootstrap a new codebase

Ordered. Each step has a done-check.

1. **Config.** Write `orbit.toml`: project root, include globs, one language block per language used, units and layers. *Done when:* `orbit status` lists the units and languages.
2. **Language adapters.** Implement or configure parse for each language. *Done when:* a 10-file sample parses with `ok` status for >=95% of files, and the golden fixture passes.
3. **Index.** Build the index; confirm incremental refresh is under 100 ms warm. *Done when:* `orbit index` twice produces identical output and the second run parses zero files.
4. **Observe.** `observe <unit>` prints layers, imports, types. *Done when:* output matches a hand-checked unit.
5. **Router.** Write `routes.toml` with the top ten questions a person asks, each with a command and a tier. *Done when:* `orbit how` routes each of the ten correctly in a test.
6. **Request log.** Every command logs. *Done when:* `orbit review` produces a report from a week of use.
7. **Artifacts.** Write the artifact ladder for one unit type. *Done when:* `orbit artifacts <unit>` shows present/missing/stale with no false "present".
8. **Principles.** Write five rules with predicates, status `observed`. *Done when:* `principles check` reports violations with file:line.
9. **Packet.** Build packets for one unit type, budgeted, cited. *Done when:* a packet for three sample tasks stays under budget and every line has a citation.
10. **Verify.** Wire the project's real checks (typecheck, unit tests, lint) through the check adapter. *Done when:* a failed check exits non-zero and writes a result file.
11. **Analyses.** Add UI usage or other domain analyses only after steps 1-10 are green.
12. **Learning, trials, climb.** Last. Each needs real usage data first.

Do not start step 11 before the index is trusted (step 3 golden test green).

## 12. Acceptance criteria for the whole system

- Same repo state and config give byte-identical `index`, `observe` and `packet` output across two machines.
- Every answer cites a source (file:line or artifact id).
- A stale artifact is reported as stale, never as present.
- A missing external source is `unavailable`, never silently empty.
- A new codebase needs only config and adapter code: zero changes in the core for the bootstrap steps.
- Routing accuracy on the test questions is measured and reported; a regression fails the test.

## 13. Decisions (resolved from the draft's gaps)

The draft listed gaps in the reference implementation. This spec closes them as follows; the reference implementation conforms.

- Language adapters are tied to a grammar per language; a new language needs an adapter and a golden test. The reference implementation ships regex adapters only (parity suite deferred, no grammar dependency).
- Platform layers live in config only (`layers` come from directory names; no per-kind code).
- Artifact paths resolve from config only, relative to the project root. No absolute paths under a user's home.
- The predicate set is closed and pinned (section 9.1).
- JSON Lines for append-only logs (request log, experience), TOML for config and curated data (routes, principles, lessons).
- Router accuracy is measured against a labelled question set; a drop below the recorded floor fails the test.

## 14. Open decisions (defaults chosen)

1. Is the index one SQLite file per repo or per unit? **One per repo; units are a query dimension.**
2. Should principle status `enforced` be per rule or per unit? **Per rule, with a unit scope.**
3. Who owns the router words: the engineers or the request-log review? **The review proposes, a person merges.**
4. Are external sources (IR, Figma) required for `verify`, or optional? **Optional, reported as `unavailable`.**
