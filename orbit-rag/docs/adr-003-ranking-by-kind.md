# ADR-003: Rank by document kind, link docs to code, propose decisions

Status: accepted (2026-10-25)

## Context

A user asked a production monorepo's orbit-rag MCP "what do the docs say about bridge
transfer cost basis?". The answer came back weak:

- **Wrong documents on top.** The top hits were agent troubleshooting checklists
  and a marketing email. The canonical spec section came 3rd, and the two
  decision notes that settle the question weren't in the top 8.
- **No authority.** The repo had no approved highlights, so nothing came back
  authoritative.
- **No path to the code.** The notes name functions in backticks, but nothing
  led to their code.
- **Nothing to judge by.** Fused scores (0.02 vs 0.03) told the agent nothing,
  and it summarised snippets without reading the sources.

Every file counted the same. A fix has to work for any repo, not just one.

## Decision

- **Kinds from generic conventions** (`kinds.rs`).
  - The kinds: decision, spec, doc, code, history, agent, marketing.
  - Each comes from conventions any repo can follow:
    - frontmatter `type:` and `status:`;
    - ADR names and decision folders;
    - `Status:` lines (superseded → history);
    - dated and log-style files;
    - agent instruction files and dot-dirs;
    - marketing and email folders;
    - `docs/` and README.
  - The kind is stored per chunk (migration 0006). A repo can override rules and
    weights in `.orbit-rag/kinds.json`.
- **Kind priors.**
  - The fused score is multiplied by decision ×1.3 and spec ×1.15; everything
    else is ×1.0.
  - The prior re-orders 3× the requested candidates.
  - Chunks with under 6 words of prose (link footers) count ×0.5.
- **Exact words first.** Prefix matching is a fallback used only when exact
  words find fewer than 5 candidates.
- **Doc-to-code links.** Names a passage puts in backticks resolve to the code
  chunk that defines them (migration 0007 indexes `chunks.symbol`), or to
  constant and env-var tags. Shown as `code_refs` and "Implemented in".
- **Proposed decisions.**
  - `context propose` turns each decision doc's decision passage into a
    *proposed* record with a review item. The installer runs it.
  - A person approves it in Review, and only then is it authoritative.
- **Agent guidance.**
  - Every hit has `kind` and `match` (strong, partial or weak retriever agreement).
  - A `read_source` MCP tool reads cited lines.
  - The server instructions say: for questions, start with
    `build_context_bundle`, and read the cited passage before asserting.

## Evidence

The full tables are in `benchmarks/RESULTS.md` §8.

- **The test set.** 31 questions from that repo about canonical docs, chosen by an
  explicit file list the classifier never sees. Plus 30 questions from random
  doc passages, to catch collateral damage. Both are split into tune and held.
- **Weights.** Chosen on the tune split.
  - Also demoting history and agent docs (×0.8/×0.75) cost more on the random
    set than it gained.
  - Heading weighting (×3) didn't help either set, so it's off.
- **Held split, MRR.**
  - Canonical: 0.47 → **0.62** (R@1 0.27 → 0.47, R@5 0.73 → 0.87).
  - Random passages: 0.49 → 0.46.
- **No-convention repo.** The Rust Book, where every file is `doc`, is unchanged.
- **Regression caught.** On the Tidepool agent benchmark, always-on prefix matching
  (added earlier the same day) reshuffled tight bundles: 39 → 30 of 45 on
  consistent tasks at 500 tokens. It was traced with `--dump` diffs and fixed by
  the exact-first fallback.

## Not built

- **Learned weights per repo.** The weights are global defaults; `kinds.json` is
  the manual knob.
- **Shared approvals.** Proposals and approvals live in the local `.orbit-rag/`
  database, so another machine re-proposes but doesn't inherit approvals.
- **Catching stale docs that nobody marked.** A stale doc that isn't marked
  superseded, dated or log-style is still `spec`.
