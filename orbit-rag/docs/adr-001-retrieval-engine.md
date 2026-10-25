# ADR-001: Retrieval engine — SQLite by default, LanceDB as an optional adapter

Status: accepted (2026-10-24)

## Context

The plan asked for a spike before choosing LanceDB: compare it with SQLite FTS5
plus SQLite-stored vectors. If one embedded database meets the targets, dropping
the second store might be worth more than what LanceDB adds.

The spike: the same 1,090 chunks from the Rust Book (112 files) and 120 labelled
queries. Six engine setups were scored. The "lance-" setups use a LanceDB 0.40
mirror built from SQLite. Synthetic vectors at 10k–200k chunks measured scaling.
Full numbers are in `benchmarks/RESULTS.md`.

## Findings

- **Quality is a tie.** Vector-only is identical in both engines (R@10 0.81,
  nDCG@10 0.56): same vectors, same flat scan. LanceDB's lexical search beats FTS5
  slightly (R@10 0.66 vs 0.63). Hybrid is 0.82 for SQLite and 0.80 for LanceDB.
  Removing stopwords from the FTS5 query did not close the lexical gap, so it was
  reverted.
- **Latency at this size is a tie.** Both answer in single-digit milliseconds,
  10–100× under the targets.
- **Scale separates them.** SQLite brute force grows linearly: 12 ms at 10k chunks,
  133 ms at 100k, 270 ms at 200k. The vector leg alone reaches the 150 ms hybrid
  budget at roughly 100–110k chunks. LanceDB's flat scan is 5–9× faster (17 ms at
  100k, 43 ms at 200k) before any ANN index.
- **LanceDB costs:**
  - the stripped binary grows from 11 MB to 208 MB;
  - a cold build goes from about 1 min to 7.5 min and needs `protoc`;
  - peak RSS goes from 29 MB to 65–90 MB;
  - lancedb 0.39–0.40 does not compile without the `remote` feature (an upstream
    feature-gating bug);
  - it adds a second store to keep in sync. In orbit-rag that is a full rebuild keyed on
    a generation stamp: about 0.4 s per 100k rows.

## Decision

SQLite (FTS5 plus stored vectors) is the default and the only required store.
LanceDB stays a feature-gated adapter (`--features lance`, `ORBIT_RETRIEVER=lance`)
behind the same `Retriever` trait, with no change to the viewer, data model, CLI or
MCP.

## When to revisit

Switch the default, or add `sqlite-vec` first, when any of these holds:

- a store passes about 75k embedded chunks, leaving headroom below the measured
  ~100k crossover;
- hybrid p95 exceeds 100 ms on real data;
- the ANN indexes or multi-vector features become needed.

At that point the cheaper first step is an in-memory vector cache for long-lived
processes (the server and MCP), because SQLite row reads dominate the cost, not the
arithmetic.
