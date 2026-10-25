-- Diff hunks corpus (derived index: rebuilt by `orbit diff-index`).
CREATE TABLE diff_hunks (
  id        INTEGER PRIMARY KEY,
  base      TEXT NOT NULL,                    -- git revisions as given/resolved
  head      TEXT NOT NULL,
  path      TEXT NOT NULL,
  old_start INTEGER NOT NULL,
  old_lines INTEGER NOT NULL,
  new_start INTEGER NOT NULL,
  new_lines INTEGER NOT NULL,
  text      TEXT NOT NULL,                    -- unified hunk incl. header; '-' old side, '+' new side
  hash      TEXT NOT NULL,
  UNIQUE (base, head, path, old_start, new_start)
);
CREATE VIRTUAL TABLE diffs_fts USING fts5(hunk_id UNINDEXED, path, text, tokenize = 'porter unicode61');
