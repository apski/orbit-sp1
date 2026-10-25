# orbit-rag

A local-first context engine with a Markdown and code viewer. You highlight
decisions and constraints in specs and code. orbit-rag re-anchors them when the
source changes, flags any it can't place safely, and gives agents source-cited,
token-budgeted context bundles. You can use it from a CLI or from read-only MCP
tools.

Not to be confused with **Orbit** (orbit-SP1's `orbit/`), the deterministic code
router, which owns `<repo>/.orbit/`. This tool keeps its index in `.orbit-rag/`.

It is one Rust process. SQLite is the only required store. Retrieval sits behind
the `Retriever` trait, with an optional LanceDB adapter. Measured results are in
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md), and the engine decision is in
[`docs/adr-001-retrieval-engine.md`](docs/adr-001-retrieval-engine.md).

## Use

**Set up a repo (any machine):** `bin/orbit install <repo> --only orbit-rag` from the orbit-SP1 checkout (or
`node orbit-rag/install.mjs <repo> [--port 7420]`). It builds and installs the binary, indexes the repo, registers the
MCP server and runs the search UI as a LaunchAgent on 127.0.0.1:7420. Manual equivalent:

```sh
cargo build --release && (cd apps/viewer && npm i && npm run build)   # optional features: lance, rerank, ann
ollama pull nomic-embed-text                 # local embeddings (optional; lexical works without)
cd your-repo && orbit-rag init                # creates .orbit-rag/ (an old .orbit/ store moves itself)
orbit-rag add docs/*.md src/lib.rs               # Markdown and code: rs, ts/tsx, js/jsx, py
orbit-rag viewer docs/navigation.md              # 127.0.0.1:7420 — select, right-click (or h) to capture
orbit-rag capture docs/x.md --text "..." --category decision [--proposed]
orbit-rag context search "who controls screen routing" [--scope architecture/navigation] [--code] [--json]
orbit-rag context bundle --task "Implement native navigation" --tokens 4000 [--code] [--format json]
orbit-rag context model [--scope core/retrieval] [--json]  # code map: types, constants, config, API, boundaries
orbit-rag check --drift                          # code constants that docs state differently (exit 1 on drift)
orbit-rag context show|source|related ctx_001
orbit-rag context link ctx_004 supersedes ctx_001          # supports, contradicts, depends_on, implements, relates_to
orbit-rag context review list|show|accept|retire ctx_002
orbit-rag context impact docs/navigation.md
orbit-rag diff-index HEAD~20 [--head worktree]   # git hunks as a "historical" corpus
orbit-rag mcp                                    # read-only MCP server on stdio
orbit-rag watch | sync | reindex | check | backup | restore <file>
```

- **Freshness.** Every command syncs the registered sources first: a new immutable
  revision, then re-anchoring, then review flags. It also drains the embedding
  outbox. The viewer polls the open file every 2 s. `orbit-rag watch` keeps the index
  current between commands.
- **Code is opt-in.** Search and bundles leave out code chunks, code diffs and
  highlights on code unless you pass `--code` (`include_code` in MCP, a checkbox
  in the viewer). Code is still tagged: see
  [`docs/adr-002-code-tags.md`](docs/adr-002-code-tags.md).
- **Ranking by document kind.** Every source gets a kind from conventions any repo
  can follow: `decision` (ADRs, decision folders, `type: decision`), `spec`
  (`docs/`, README), `history` (dated, log-style or superseded files), `agent`
  (`AGENTS.md`, `SKILL.md`, dot-dirs), `marketing`, `doc` and `code`.
  - Decisions (×1.3) and specs (×1.15) rank higher; nothing is demoted.
  - Chunks that are only links (a `Related: [[…]]` footer) count half.
  - Override per repo with `.orbit-rag/kinds.json`:
    `{"rules": [["path/prefix/", "decision"]], "weights": {"agent": 0.8}}`.
  - Decision ADR: [`docs/adr-003-ranking-by-kind.md`](docs/adr-003-ranking-by-kind.md).
- **Each hit carries:**
  - `kind`;
  - `match`: `strong` when every retriever ranked it in its top 10, `partial` or `weak` otherwise;
  - `code_refs`: where the code names it puts in backticks are defined, even with code off.
- **Decisions become reviewable highlights.**
  - `orbit-rag context propose [--dry-run]` offers each decision doc's decision
    passage as a *proposed* record. The installer runs it.
  - Approve in the viewer's Review tab, or with `context review accept <id>`.
  - Nothing is authoritative until a person approves it.
- **Engines and settings:**

  | variable | values | default |
  |---|---|---|
  | `ORBIT_EMBED` | `ollama:<model>`, `openai:<model>`, `hash`, `none` | `ollama:nomic-embed-text` |
  | `ORBIT_RETRIEVER` | `sqlite`, `lance` | `sqlite` |
  | `ORBIT_RERANK` | `jina-turbo`, `bge-base`, `none` (needs `--features rerank`) | `none` |
  | `ORBIT_TOKENIZER` | `chars4`, `o200k`, `cl100k` | `chars4` |
  | `ORBIT_ANN_MIN` | vectors per model before the HNSW index is used (needs `--features ann`) | 20000 |
  | `ORBIT_KIND_PRIORS` | `0` turns off the kind weights and the thin-chunk rule | on |
  | `ORBIT_FTS_PREFIX` | `0` turns off the prefix fallback (words of 3+ letters, used when exact words find under 5 hits) | on |
  | `ORBIT_HEADING_WEIGHT` | bm25 weight of headings vs body (3.0 didn't help) | 1.0 |
- **MCP.** `orbit-rag mcp` is a read-only stdio server with 9 tools:
  `build_context_bundle` (start here for questions), `search_context`,
  `read_source` (path + lines), `get_context_record`, `get_context_source`,
  `related_context`, `review_queue`, `impact` and `code_model`. Its instructions
  tell agents to read the cited passage before stating specifics.
  - **Register it** from the indexed repo's root:
    `claude mcp add orbit-rag -s local -e ORBIT_EMBED=ollama:nomic-embed-text -- <abs path>/orbit-rag mcp`.
  - **Use an installed copy** (for example `~/.local/share/orbit-rag/bin/orbit-rag`),
    not `target/release/orbit-rag`. macOS kills a running process when `cargo build`
    rewrites its binary.
  - **Freshness.** Each call syncs changed files and embeds for at most 2 s. Run
    `orbit-rag sync` to drain a large backlog, for example after the first `orbit-rag add`
    on a big repo.
- **In use.** A 4,458-file production repo was set up with
  `install.mjs`. Its search UI is http://127.0.0.1:7420. The skill is `orbit-rag`
  (`skills/orbit-rag`; `bin/orbit skills-sync` keeps the global copies). Update the
  skill and this README when the tools or setup here change.

## Layout

| Plan crate | Here |
|---|---|
| core, storage | `crates/core/src/storage.rs`, `migrations/` (append-only snapshots, revisions, anchors; events) |
| sources | `sources.rs` (Markdown AST chunks, rename detection by hash or line similarity), `code.rs` (tree-sitter symbols), `diffs.rs` |
| code tags | `tags.rs` (subsystem + kind tags, derived on reindex), `model.rs` (code model, drift check) |
| anchors | `anchors.rs`: a pure resolver with unit fixtures |
| retrieval | `retrieval.rs` (FTS5 + vector → RRF across corpora → optional rerank), `embed.rs` (providers, cache, outbox), `ann.rs` (usearch HNSW), `crates/lance` (LanceDB adapter), `crates/rerank` (fastembed cross-encoder) |
| review, relationships | `review.rs` |
| bundles | `bundles.rs`: relevance-packed, budget covers the rendered output, sentence context for highlights |
| cli, server, MCP | `crates/cli`: clap, tiny_http on loopback with a Host check and JSON-only POSTs, `mcp.rs`, `ops.rs` |
| viewer | `apps/viewer`: React; a rehype plugin maps DOM selections to source offsets; code view |
| benchmarks | `crates/bench` (`orbit-bench anchors / retrieval / perf / scale / agent / drift / tokens`), `benchmarks/`, `fixtures/agent-repo`, `fixtures/tidepool-src` |

## Anchor resolution

The resolver tries these in order:

1. A line diff, if the selection sits inside an unchanged run.
2. A unique match of the text plus its block-local context.
3. A match that ignores whitespace (a re-wrapped paragraph).
4. A fuzzy match.

Only `exact` and `relocated` are confident. Duplicates get special care:

- A *new* copy is accepted only if the diff and the wider surrounding text pick
  the same copy.
- If identical copies already existed in the old text, losing one of them is
  always `ambiguous`.

Everything else (`context_changed`, `changed`, `ambiguous`, `orphaned`, `missing`)
opens a review item. The record then shows as `needs_review` and is never treated
as authoritative. On 6,600 seeded edits there were zero wrong confident
attachments.

## Not built, or built and not worth it

- **Rerank.** The `Reranker` stage exists, and a fastembed cross-encoder is behind
  `--features rerank`. Measured: +1 nDCG point at 13–31 s per query and 21–45 GB
  RSS on CPU (`benchmarks/RESULTS.md` §2). Don't enable it until a lighter runtime
  or model shows a real gain on `orbit-bench retrieval`.
- **Exact token counts.** chars/4 is the default; `ORBIT_TOKENIZER=o200k|cl100k`
  is steadier per chunk. No public tokenizer matches Gemma or Claude (§6).
- **ANN at scale.** The HNSW index (`--features ann`) is built and matches exact
  search on real embeddings at 1k vectors. The 10k–200k latency and recall run is
  pending (§7). Until then the default stays the exact scan, which is fine to
  about 100k chunks.
- **Level-3 tags (rationale).** Code tags stop at subsystem and kind. A model
  proposing "why" records into Review is the next step if the deterministic tags
  leave gaps (ADR-002).
- **File watching.** `watch` and the viewer poll (one hash per source per tick)
  rather than using FSEvents/inotify.
