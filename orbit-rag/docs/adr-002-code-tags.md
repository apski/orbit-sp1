# ADR-002: Machine tags for code, kept out of search by default

Status: accepted (2026-10-25)

## Context

The idea was to scan code and tag it automatically, at a few levels such as
constraint and subsystem. Two concerns came with it:

- **Pollution.** Code and machine tags should not pollute answers about docs and
  decisions, so code needs a toggle.
- **Authority.** The small-model agent result depends on human-approved records
  being labelled authoritative. A scanner that produces records would dilute
  that label.

## Decision

- **Tags are derived data, not records.** A `tags` table (migration 0004)
  hangs off `chunks`. It is rebuilt on every reindex, so it can't go stale or
  conflict with human edits. Nothing in it is ever authoritative. Human judgement
  stays in records, through capture and review.
- **Tags never feed ranking.** They are not in the FTS index or the embedding
  text, so search and bundles score exactly as before.
- **Levels**, all deterministic (`crates/core/src/tags.rs`):
  1. Subsystem, from the path (`crates/core/src/retrieval.rs` → `core/retrieval`).
  2. Kind. Each comes from tree-sitter or a line scan:

     | kind | what it finds |
     |---|---|
     | `constant` | name and literal value |
     | `type` | name and members |
     | `config` | env-var reads |
     | `boundary` | process, network, fs or SQL calls |
     | `check` | guards and asserts |
     | `api` | `pub` / `export` symbols |
     | `ceiling` | TODO, FIXME, `ponytail:` notes |

     Test files and `mod tests` are skipped.
  3. Rationale ("why"). This needs judgement, so it is not built. If it is added,
     a model may only *propose* records into the Review queue.
- **The code toggle.** `SearchQuery.include_code` defaults to false. The filter
  runs in SQL on a new `chunks.lang` / `diff_hunks.lang` column, so code can't
  crowd docs out of the candidate pool. Highlights on code are filtered after
  ranking. To include code, use:
  - `--code` on the CLI;
  - `include_code` on the MCP tools;
  - the "Include code" checkbox in the viewer.
- **Two uses of the tags**:
  - `orbit-rag context model` (and the MCP tool `code_model`): a per-subsystem map of
    types, constants, config, public API, boundaries and ceilings, with a
    citation for each item.
  - `orbit-rag check --drift`: numeric constants compared with the docs that state
    them. Changelogs, incidents and meeting notes are labelled `history`, not
    `drift`.

## Measured

Full numbers are in `benchmarks/RESULTS.md` §5.

- **Tidepool fixture.** The docs plus a small TypeScript tree with 9 planted
  contradictions and 6 expected historical disagreements, labelled before the
  checker first ran.
  - First version: precision 0.65, recall 0.87.
  - After fixing sentence splitting at decimal points and making the matcher
    unit-aware: precision 0.93, recall 0.87.
- **orbit-rag's own repo** (31 files, not used for tuning): 0 false alarms.
- **Misses** are synonyms: "password length" vs "8-character passwords", and
  "page size" vs "items per page".

## Not built

- Clustering subsystems by import graph. Paths are enough until a repo's
  directories stop matching its subsystems.
- Drift checks for string constants (URLs, versions).
- SQL schema in the code model: migrations are not a supported source type.
