-- Canonical store. Snapshots, source revisions, record revisions and anchors are
-- append-only; "current" state is always the latest row. Everything under
-- "derived index" can be dropped and rebuilt with `orbit reindex`.

CREATE TABLE sources (
  id         TEXT PRIMARY KEY,                -- src_001; identity survives renames
  path       TEXT NOT NULL UNIQUE,            -- current repo-relative path
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE snapshots (                      -- content-addressed
  hash    TEXT PRIMARY KEY,                   -- sha256 of content
  content TEXT NOT NULL
);

CREATE TABLE source_revisions (
  id            INTEGER PRIMARY KEY,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  snapshot_hash TEXT NOT NULL REFERENCES snapshots(hash),
  path          TEXT NOT NULL,
  git_rev       TEXT,                         -- HEAD when observed, "+dirty" if worktree differs
  observed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX source_revisions_source ON source_revisions(source_id, id);

CREATE TABLE records (
  id         TEXT PRIMARY KEY,                -- ctx_001
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE record_revisions (
  id         INTEGER PRIMARY KEY,
  record_id  TEXT NOT NULL REFERENCES records(id),
  rev        INTEGER NOT NULL,
  category   TEXT NOT NULL CHECK (category IN ('decision','constraint','insight','note','question')),
  body       TEXT NOT NULL,
  note       TEXT,
  scope      TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('active','needs_review','superseded','retired')),
  authority  TEXT NOT NULL CHECK (authority IN ('human_approved','proposed')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (record_id, rev)
);

CREATE TABLE anchors (
  id                 INTEGER PRIMARY KEY,
  record_id          TEXT NOT NULL REFERENCES records(id),
  source_revision_id INTEGER NOT NULL REFERENCES source_revisions(id),
  exact              TEXT NOT NULL,
  prefix             TEXT NOT NULL,
  suffix             TEXT NOT NULL,
  start_offset       INTEGER,                 -- byte offsets; NULL when unresolved
  end_offset         INTEGER,
  heading_path       TEXT NOT NULL,           -- JSON array
  resolution         TEXT NOT NULL CHECK (resolution IN
    ('captured','exact','relocated','context_changed','changed','ambiguous','orphaned','missing','accepted')),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX anchors_record ON anchors(record_id, id);

CREATE TABLE edges (
  id         INTEGER PRIMARY KEY,
  src        TEXT NOT NULL REFERENCES records(id),
  kind       TEXT NOT NULL CHECK (kind IN ('supports','contradicts','depends_on','supersedes','implements','relates_to')),
  dst        TEXT NOT NULL REFERENCES records(id),
  provenance TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (src, kind, dst),
  CHECK (src <> dst)
);

CREATE TABLE review_items (
  id          INTEGER PRIMARY KEY,
  record_id   TEXT NOT NULL REFERENCES records(id),
  reason      TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN ('open','accepted','retired')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  resolved_at TEXT
);
CREATE UNIQUE INDEX review_one_open ON review_items(record_id) WHERE state = 'open';

CREATE TABLE events (
  id     INTEGER PRIMARY KEY,
  at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  kind   TEXT NOT NULL,
  entity TEXT NOT NULL,
  data   TEXT NOT NULL
);

CREATE VIEW current_records AS
SELECT r.id, rr.rev, rr.category, rr.body, rr.note, rr.scope, rr.status, rr.authority,
       a.id AS anchor_id, a.source_revision_id, a.exact, a.prefix, a.suffix,
       a.start_offset, a.end_offset, a.heading_path, a.resolution,
       sr.source_id, s.path, sr.snapshot_hash, sr.git_rev,
       (SELECT MAX(id) FROM source_revisions WHERE source_id = sr.source_id) AS latest_rev_id
FROM records r
JOIN record_revisions rr ON rr.id = (SELECT MAX(id) FROM record_revisions WHERE record_id = r.id)
JOIN anchors a ON a.id = (SELECT MAX(id) FROM anchors WHERE record_id = r.id)
JOIN source_revisions sr ON sr.id = a.source_revision_id
JOIN sources s ON s.id = sr.source_id;

-- derived index ---------------------------------------------------------------

CREATE TABLE chunks (                         -- source_chunks corpus (latest revision only)
  id           INTEGER PRIMARY KEY,
  revision_id  INTEGER NOT NULL REFERENCES source_revisions(id),
  heading_path TEXT NOT NULL,
  scope        TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset   INTEGER NOT NULL,
  text         TEXT NOT NULL,
  hash         TEXT NOT NULL                  -- embedding cache key
);
CREATE TABLE record_index (                   -- context_records corpus
  record_id TEXT PRIMARY KEY REFERENCES records(id),
  hash      TEXT NOT NULL
);
CREATE VIRTUAL TABLE chunks_fts  USING fts5(chunk_id UNINDEXED, heading, text, tokenize = 'porter unicode61');
CREATE VIRTUAL TABLE records_fts USING fts5(record_id UNINDEXED, heading, body, note, tokenize = 'porter unicode61');

CREATE TABLE embed_outbox (                   -- written in the same tx as the content
  hash TEXT PRIMARY KEY,
  text TEXT NOT NULL
);
CREATE TABLE embeddings (
  hash  TEXT NOT NULL,
  model TEXT NOT NULL,                        -- provider/model@version
  vec   BLOB NOT NULL,                        -- f32 LE, L2-normalised
  PRIMARY KEY (hash, model)
);
