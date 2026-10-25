# Benchmark results

Measured on 2026-10-24, Apple Silicon Mac, release build. The raw JSON for every
run is in `results/`. Rerun with `./benchmarks/run.sh [anchors|retrieval|perf|agent]`.
Embeddings come from `ollama:nomic-embed-text`, run locally.

## 1. Anchoring (`results/anchors.json`)

The fixtures are real Rust Book paragraphs. Each case takes a random selection of
3–12 words and applies one seeded edit; the expected outcome is known by
construction. There are 300 cases per mutation and 3,300 per seed.

| mutation | seed 1 correct | false confident | false alarm | typical outcome |
|---|---|---|---|---|
| insert_before | 300 | 0 | 0 | relocated |
| insert_after | 300 | 0 | 0 | exact |
| delete_other | 300 | 0 | 0 | exact / relocated |
| move_block | 300 | 0 | 0 | relocated |
| compound (3 edits) | 300 | 0 | 0 | relocated |
| duplicate_block | 300 | 0 | 0 | ~90% kept original confidently, ~10% ambiguous (adjacent paste) |
| delete_dup_original | 300 | 0 | 0 | ambiguous |
| edit_inside | 300 | 0 | 0 | changed → review |
| edit_context | 300 | 0 | 0 | context_changed → review |
| delete_block | 300 | 0 | 0 | orphaned / changed → review |
| reflow_inside | 299 | 0 | 1 | relocated |
| **total** | **3,299 / 3,300** | **0** | **1** | |

- **Hard gate:** zero wrong confident attachments, on seed 1 and on seed 2
  (seed 2: 3,294 correct, 0 wrong, 6 false alarms).
- **Latency per resolve:** p50 0.07 ms, p95 12.7 ms, max 56 ms. The tail comes from
  the fuzzy search used for edited selections.
- **What the benchmark caught.** The first version of the resolver attached
  confidently to the surviving copy when one of two identical paragraphs was
  deleted: 300 of 300 `delete_dup_original` cases were wrong. It also sent every
  re-wrapped selection to review (300 false alarms). Both are fixed and covered by
  unit tests.

## 2. Retrieval (`results/retrieval-*.json`)

- **Corpus:** 112 Rust Book files, 1,090 chunks.
- **Queries:** 120 labelled queries across 64 files. gemma4:26b generated them
  doc2query-style, one source passage each. The median share of query content
  words that appear in the passage is 0.50; 31 queries are at or below 0.3.
- **Latency:** the query embedding is computed in advance and excluded.

| config | R@1 | R@5 | R@10 | MRR@10 | nDCG@10 | p50 ms | p95 ms | peak RSS MB |
|---|---|---|---|---|---|---|---|---|
| SQLite FTS5 (whole words, 10-24) | 0.23 | 0.50 | 0.63 | 0.34 | 0.41 | 1.6 | 2.1 | 29 |
| SQLite FTS5, prefix match (10-25) | 0.22 | 0.51 | 0.63 | 0.34 | 0.41 | — | 10.5* | — |
| SQLite vector | 0.31 | 0.70 | 0.81 | 0.48 | 0.56 | 2.7 | 4.5 | 29 |
| SQLite hybrid (RRF) | 0.31 | 0.69 | **0.82** | 0.46 | 0.55 | 5.0 | 7.0 | 30 |
| SQLite hybrid, prefix match (10-25) | 0.29 | 0.69 | 0.83 | 0.46 | 0.55 | — | 17.6* | — |
| SQLite vector via HNSW (`ann`) | 0.31 | 0.70 | 0.81 | 0.48 | 0.56 | — | 3.3* | — |
| SQLite hybrid + jina-turbo rerank (`results/rerank.json`) | 0.33 | 0.70 | 0.80 | 0.48 | 0.56 | 12,990 | 44,700* | 21,095 |
| LanceDB FTS | 0.24 | 0.54 | 0.66 | 0.36 | 0.43 | 1.2 | 2.0 | 62 |
| LanceDB vector | 0.31 | 0.70 | 0.81 | 0.48 | 0.56 | 1.8 | 2.2 | 66 |
| LanceDB hybrid | 0.28 | 0.67 | 0.80 | 0.45 | 0.53 | 1.3 | 3.5 | 75 |

\* Measured on 2026-10-25 while other jobs saturated the host (another session's
Xcode builds, model runs, swap in use). Treat these latencies as upper bounds; the
quality columns are unaffected.

- **Prefix matching.** Words of 3+ characters match as prefixes, so a search
  with no models running finds "Firebase" from "fire". On the labelled queries,
  quality is unchanged.
- **HNSW on real embeddings.** Identical to the exact scan here, but at 1,090
  vectors HNSW is effectively exact, so this shows correctness, not recall at scale.
- **Reranker** (`--features rerank`, fastembed ONNX on CPU):
  - jina-turbo gained 1 nDCG point (0.56 vs 0.55) and lost 2 points of R@10.
  - It took 13 s per query (p50) and 21 GB RSS.
  - bge-base on 10 queries took 31 s per query and 45 GB RSS.
  - Memory grows with each batch regardless of the 512-token input cap. My
    unconfirmed guess is the ONNX graphs or ONNX Runtime's memory arena. A
    second run exhausted swap on the shared host and was stopped.
  - **Verdict:** not worth it. The `Reranker` stage stays in the code as an
    extension point; the fastembed adapter is experimental and off by default.

Hybrid with relationship expansion could not be measured: the Rust Book has no
curated records or edges to expand. Expansion is exercised in the agent benchmark.

### Vector search vs corpus size (`results/scale.json`)

Synthetic 768-d unit vectors, a flat scan, and 30 queries per size.

| chunks | SQLite p95 | LanceDB p95 | LanceDB mirror build |
|---|---|---|---|
| 10k | 13 ms | 3 ms | 0.05 s |
| 50k | 69 ms | 9 ms | 0.2 s |
| 100k | 136 ms | 20 ms | 0.4 s |
| 200k | 274 ms | 46 ms | 0.8 s |

These numbers drove the engine decision: see `docs/adr-001-retrieval-engine.md`.

## 3. Latency targets (`results/perf.json`)

Warm runs are in-process, 200 iterations after 10 warm-up calls. Cold runs use a
fresh `orbit-rag` process per call, which includes the store open and a sync of all
112 sources.

| operation | target p95 | p50 | p95 | result |
|---|---|---|---|---|
| highlight save | 50 ms | 0.4 ms | 0.6 ms | pass |
| lexical retrieval | 30 ms | 2.3 ms | 3.6 ms | pass |
| hybrid retrieval, SQLite | 150 ms | 4.6 ms | 6.4 ms | pass |
| hybrid retrieval, LanceDB | 150 ms | 1.3 ms | 2.4 ms | pass |
| 4k-token bundle | 200 ms | 5.8 ms | 8.1 ms | pass |
| open Markdown file, server (`GET /api/doc`) | 200 ms | 0.4 ms | 0.5 ms | pass |
| open Markdown file, in browser (click → rendered, 40 chapters) | 200 ms | 19 ms | 41 ms | pass |
| lexical search, cold CLI | — | 15 ms | 16 ms | |
| 4k bundle, cold CLI | — | 12 ms | 15 ms | |

## 4. Agent trials (`results/agent-*.json`)

- **Repo:** a fictional mobile-app repo (`fixtures/agent-repo`, 23 Markdown files) with 33
  human-curated highlights. No model can know its specifics.
- **Tasks:** 37 questions, each with a known answer and known wrong values.
  - *consistent* (15 tasks): the docs agree. Traps include a rejected proposal in
    meeting notes, old values in the changelog, a deprecated token, and a spec
    edited after its highlight was approved.
  - *doc_rot* (22 tasks, up from 5): docs disagree and nothing in the text says
    which one wins. Ground truth is the human-approved decision. The authority
    isn't always in the formal-looking doc: approved answers sit in the glossary,
    onboarding, an ADR, the architecture doc and others; stale ones sit in API
    docs, notification docs and the support matrix.
- **Setup:** 3 trials per task and condition, temperature 0.6. Raw RAG and the
  bundle get the same token budget. Full docs is about 4.1k tokens.

**What this measures.** Every doc_rot task has an approved highlight on the
right answer, and the fixture's author (me) placed them. So this tests whether a
bundle carries human authority through to the answer *when a human approved the
right statement*. It does not test whether people curate in practice.

### Accuracy, doc_rot split (correct / 66 runs)

| model | budget | no context | full docs | raw RAG | orbit-rag bundle |
|---|---|---|---|---|---|
| gemma3:4b | 500 | 8 | 45 | 39 | **63** |
| gemma3:4b | 1200 | 8 | 45 | 36 | **60** |
| gemma4:26b | 500 | 9 | 45 | 48 | **63** |
| gemma4:26b | 1200 | 9 | 45 | 48 | **66** |

### Accuracy, consistent split (correct / 45 runs)

| model | budget | no context | full docs | raw RAG | orbit-rag bundle |
|---|---|---|---|---|---|
| gemma3:4b | 500 | 15 | 42 | **45** | 39 |
| gemma3:4b | 1200 | 15 | 42 | **45** | 42 |
| gemma4:26b | 500 | 21 | **45** | **45** | 42 |
| gemma4:26b | 1200 | 21 | **45** | **45** | **45** |

Prompt tokens per call: no context about 90; full docs about 4,150; raw RAG
640 / 1,385 (500 / 1200 budget); bundle 690 / 1,670. The chars/4 estimate of the
whole prompt was within 0–5% of the model's count.

**Reading this honestly:**

- **Doc rot.** With 22 tasks the earlier 5-task signal holds and grows. Bundles
  beat raw RAG by 15–24 of 66 runs and full docs by 15–21, for **both** models.
  The strong model was at parity on the old 5 tasks only because those were easy.
- **Consistent docs.** Bundles *lose* 3–6 of 45 runs at tight budgets:
  - `release-day` and `tti-budget` at 500 tokens, `spacing-base` at 1200;
  - approved records fill budget that the passage with the answer needed.
  This is the remaining cost of the authority quota.
- **Model failures, not retrieval failures.** `e2e-tool` (Maestro vs Detox) fails
  with gemma3:4b even when the approved record is the first item, labelled
  authoritative.
- **A bug the run caught.** A highlight on a glossary fragment ("PostHog, EU
  cloud") was indexed without its sentence, so it never matched "which analytics
  vendor?". Records are now indexed with their sentence. gemma3:4b at 500 tokens
  went from 57/66 to 63/66 on doc_rot.
  - Before-fix run: `results/agent-gemma3-4b-b500-before-record-context.json`.
  - Raw RAG is unaffected (it has no records).
- **Previous run.** The 20-task run (5 doc_rot) of 10-24 is in git history: bundle
  15/15 and raw RAG 9–15/15 on doc_rot.

## 5. Drift check (`results/drift*.json`)

`orbit-rag check --drift` compares numeric constants in code with the doc sentences
that talk about them. It is deterministic, with no model. The fixture is
`fixtures/tidepool-src`: a small TypeScript tree whose `config.ts` disagrees with
some of the Tidepool docs. The 15 expected pairs were labelled before the checker
first ran (`benchmarks/drift_expected.json`): 9 `drift`, where a current doc
disagrees, and 6 `history`, from the changelog, incident or meeting notes.

| version | found | correct | precision | recall | wrong verdict |
|---|---|---|---|---|---|
| v1: word overlap | 20 | 13 | 0.65 | 0.87 | 0 |
| v2: + no split at decimal points, unit-aware numbers | 14 | 13 | **0.93** | **0.87** | 0 |

- **v1 false alarms.** Sentences were split at the decimal point in "2.5 seconds",
  and `TOKEN_TTL_MINUTES` matched "120 requests per minute per token". v2 counts
  only numbers followed by the constant's unit, or by `per <unit>` for rates.
- **Remaining false alarm.** "Cold start … 1.5 seconds" matches
  `BACKOFF_START_SECONDS` (start, second).
- **Misses are synonyms.** "8-character passwords" is not "password length", and
  "50 items per page" is not "page size".
- **Out of sample.** On orbit-rag's own repo (31 files, 30 numeric constants, not used
  for tuning) it raised 0 false alarms. orbit-rag's docs don't contradict its code,
  so this run says nothing about recall.
- **Cost.** 1 ms for the fixture. The 45 tags come from 3 files.

## 6. Token counting (`results/tokens.json`)

200 Rust Book chunks, compared with gemma3:4b's own count (`prompt_eval_count`
for a raw prompt):

| counter | total vs real | mean error per chunk | p95 error |
|---|---|---|---|
| chars/4 (default) | 0.96 | 9.4% | 22% |
| o200k (`ORBIT_TOKENIZER=o200k`) | 0.92 | 7.4% | 13% |
| cl100k | 0.92 | 7.4% | 13% |

- **No exact choice.** No public tokenizer matches Gemma or Claude. BPE is steadier
  per chunk but undercounts Gemma by 8% overall.
- **Defaults.** chars/4 stays the default; set o200k for OpenAI-family agents.
- **Cost.** The BPE tables add 7 MB to the stripped binary (11 → 18 MB).

## 7. Vector index at scale (`results/scale*.json`)

- **What it is.** usearch HNSW, bf16, keyed by a stable embedding id (migration
  0003), updated incrementally, with candidates re-scored exactly from SQLite. It
  is behind `--features ann` and used once a model has `ORBIT_ANN_MIN` vectors
  (default 20k).
- **Not measured yet.** The first scale run was invalid, for three reasons:
  - The host was saturated, so the exact scan read 127 ms at 10k against 13 ms on
    10-24.
  - Every query counted vectors with a full table scan. Migration 0005 adds the
    missing indexes.
  - Uniform random vectors are the worst case for HNSW (recall 0.31).
- **Next.** The bench now uses clustered vectors. The rerun waits for a quiet host.
