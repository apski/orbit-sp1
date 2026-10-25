---
type: progress
status: done
area: orbit
created: 2026-10-24
---

# 2026-10-24: orbit, the slice plus the remaining phases and the LanceDB spike

## Done

- **Vertical slice (§11):**
  - Rust core, SQLite, React viewer.
  - Capture, re-anchor/flag, hybrid search and cited bundles.
  - Verified with the CLI script and in the browser; still passing at the end of
    the session.
- **Phases 2–7:**
  - Code sources: tree-sitter chunking for Rust, TS/TSX, JS and Python by symbol and
    container, plus a code view with capture.
  - A diff-hunk corpus (`orbit diff-index`), marked "historical".
  - Renames detected even when the file was also edited (line similarity).
  - Read-only MCP server (`orbit mcp`, 7 tools, checked with a scripted JSON-RPC
    session).
  - `add`, `watch`, `check`, and `backup`/`restore` with zstd -19 (2.6× smaller).
- **LanceDB spike:**
  - An adapter behind `Retriever` (`--features lance`, `ORBIT_RETRIEVER=lance`).
  - Decision recorded in `docs/adr-001-retrieval-engine.md`: SQLite stays the
    default, LanceDB stays optional.
- **Phase 8 evaluation** (`orbit-bench`, `benchmarks/RESULTS.md`):
  - Anchoring: 6,600 seeded edits, 0 wrong confident attachments.
  - Retrieval: 120 labelled queries over the Rust Book, 6 engine configs.
  - Latency: every gate passes, by 10–100×.
  - Scaling: SQLite reaches the 150 ms budget at about 100k chunks; LanceDB is 5–9×
    faster.
  - Agent trials: on a fictional repo, 2 models × 2 budgets × 4 conditions × 3 trials.

## Findings and fixes that came out of the benchmarks

- **Anchoring.**
  - When one of two identical paragraphs was deleted, the resolver attached
    confidently to the survivor (300/300 wrong). Fixed.
  - Re-wrapping a paragraph raised false alarms. Fixed.
- **Bundles.**
  - The rendered bundle overran its budget by about 60%.
  - Per-corpus ranking let every one of the 16 curated records crowd out the
    evidence that answered the question.
  - Highlights rendered as bare fragments.
  - After all three fixes, the bundle went from 0.83 to 1.00 with gemma4:26b.
- **Agent results.**
  - With gemma4:26b, the bundle ties raw RAG (1.00 vs 1.00).
  - With gemma3:4b, the doc-rot split scores 15/15 with the bundle, 9–12/15 with raw
    RAG and 9/15 with full docs. That is the first evidence that curation helps, but
    it rests on 5 tasks.
- **LanceDB.** Lexical quality is +3 points; vector quality is identical. The
  stripped binary is 18× larger, the cold build goes from 1 min to 7.5 min, it needs
  `protoc`, and it hits an upstream `remote`-feature build bug.

## Environment changes

- `brew install protobuf`: LanceDB needs it to build.
- `ollama pull nomic-embed-text` and `gemma3:4b`.
- `benchmarks/corpus/` holds a gitignored clone of the Rust Book.

## Open

- Nothing is committed yet; the repo is git-initialised only.
- Grow the doc-rot task set (20+ tasks) before claiming an effect size.
- The reranker stage is not wired in; fastembed's bge-reranker is the candidate.
- Use a real tokenizer only if budgets need to be exact (chars/4 is within 1–8%).
