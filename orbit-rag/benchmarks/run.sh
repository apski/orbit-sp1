#!/usr/bin/env bash
# Full evaluation. Results land in benchmarks/results/*.json; RESULTS.md is written by hand from them.
#   ./benchmarks/run.sh [anchors|retrieval|perf|agent|all]
set -euo pipefail
cd "$(dirname "$0")/.."
WHAT="${1:-all}"
BIN=target/release
OUT=benchmarks/results; mkdir -p "$OUT"
cargo build --release -p orbit-rag -p orbit-bench >/dev/null
./benchmarks/fetch-corpus.sh >/dev/null
export ORBIT_EMBED="${ORBIT_EMBED:-ollama:nomic-embed-text}"

if [[ $WHAT == anchors || $WHAT == all ]]; then
  $BIN/orbit-bench anchors --cases 3000 --seed 1 > "$OUT/anchors.json"
fi
if [[ $WHAT == retrieval || $WHAT == all ]]; then
  $BIN/orbit-bench setup
  [ -s benchmarks/queries.jsonl ] || $BIN/orbit-bench gen-queries --n 120
  for c in fts5 sqlite-vector sqlite-hybrid lance-fts lance-vector lance-hybrid; do
    $BIN/orbit-bench retrieval "$c" > "$OUT/retrieval-$c.json"   # one process per config: honest peak RSS
  done
fi
if [[ $WHAT == perf || $WHAT == all ]]; then
  $BIN/orbit-bench perf > "$OUT/perf.json"
fi
if [[ $WHAT == agent || $WHAT == all ]]; then
  $BIN/orbit-bench agent --trials 3 > "$OUT/agent.json"
fi
