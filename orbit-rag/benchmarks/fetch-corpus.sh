#!/usr/bin/env bash
# Public, representative technical Markdown for benchmarks (pinned for reproducibility).
set -euo pipefail
cd "$(dirname "$0")"
REV="${BOOK_REV:-1500248d8f230566e4ec9f27fcbb8fe9e2898ab1}"
if [ ! -d corpus/book ]; then
  git clone -q --filter=blob:none https://github.com/rust-lang/book corpus/book
fi
cd corpus/book
if [ -n "$REV" ]; then git checkout -q "$REV"; fi
git rev-parse HEAD
