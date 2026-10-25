---
type: progress
status: done
area: orbit
created: 2026-10-25
---

# 2026-10-25: orbit code tags, drift check, and the "not built" list

## Done

- **Commits.** The 10-24 work went in as 6 logical commits (98fdf35..c7cbd8b).
- **Code tags (ADR-002):**
  - Level 1 subsystem (from the path) and level 2 kind (constant, type,
    config, boundary, check, api, ceiling), via tree-sitter plus line scans.
  - Derived on every reindex, never authoritative, never in ranking.
  - Migration 0004 adds `tags` and `chunks.lang` / `diff_hunks.lang`. Stores at
    v1/v2 upgrade and backfill tags on open (tested).
- **Code toggle.** Search and bundles leave code out by default, filtered in SQL.
  `--code`, MCP `include_code` and the viewer checkbox turn it on.
- **Uses of the tags:**
  - `orbit context model` and the MCP tool `code_model`: a per-subsystem map.
  - `orbit check --drift`: precision 0.93, recall 0.87 on 15 pairs labelled in
    advance; 0 false alarms on orbit itself.
- **Viewer.** A Tags tab for code: dotted underline on signature lines, shown only
  while the tab is open. Checked in the browser.
- **Doc-rot tasks: 5 → 22.**
  - Bundles beat raw RAG by 15–24 of 66 runs for both gemma3:4b and gemma4:26b.
  - They cost 3–6 of 45 on consistent tasks at tight budgets.
  - The run caught a record-indexing bug: fragments indexed without their
    sentence. Fixed: 57 → 63 of 66.
- **Tokenizer.** `ORBIT_TOKENIZER=o200k|cl100k`. Measured against gemma3's own
  counts; chars/4 stays the default.
- **Reranker.** Built (trait + fastembed adapter behind `--features rerank`) and
  rejected: +1 nDCG point at 13–31 s per query and 21–45 GB RSS.
- **HNSW (usearch).** Behind `--features ann`, with stable embedding ids
  (migration 0003) and lookup indexes (0005). Correct on real data; the scale run
  is pending.
- **FTS prefix matching.** "fire" finds "Firebase" with no models running (a user
  report). Quality on the 120 labelled queries is unchanged.

## Incidents

- **Reranker memory.** The run reached about 72 GB RSS and exhausted swap on the
  shared host, blocking another session's measurement labs. It was killed when
  that session flagged it. Don't rerun it on a shared machine.
- **Ollama thrash.** Mixed `num_ctx` across concurrent benches reloaded gemma3
  on every alternation (38 s each). The tokens bench now uses the agent bench's
  `num_ctx`.

## Open

- Rerun `orbit-bench scale` (clustered vectors, ANN) and `orbit-bench perf` on a
  quiet host. The 10-25 latencies were taken under load.
- The bundle authority quota crowds the answer out at 500 tokens
  (`release-day`, `tti-budget`).
- Drift-check synonyms (length vs characters) and string constants.
