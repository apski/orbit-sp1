-- Code toggle: search leaves code out unless asked, so it filters on the source
-- language in SQL rather than after ranking.
ALTER TABLE chunks ADD COLUMN lang TEXT NOT NULL DEFAULT 'markdown';
UPDATE chunks SET lang = (
  SELECT CASE
    WHEN s.path LIKE '%.rs' THEN 'rust'
    WHEN s.path LIKE '%.tsx' THEN 'tsx'
    WHEN s.path LIKE '%.ts' THEN 'typescript'
    WHEN s.path LIKE '%.js' OR s.path LIKE '%.jsx' OR s.path LIKE '%.mjs' OR s.path LIKE '%.cjs' THEN 'javascript'
    WHEN s.path LIKE '%.py' THEN 'python'
    ELSE 'markdown' END
  FROM source_revisions r JOIN sources s ON s.id = r.source_id WHERE r.id = chunks.revision_id);
ALTER TABLE diff_hunks ADD COLUMN lang TEXT NOT NULL DEFAULT 'markdown';
UPDATE diff_hunks SET lang = CASE
    WHEN path LIKE '%.rs' THEN 'rust'
    WHEN path LIKE '%.tsx' THEN 'tsx'
    WHEN path LIKE '%.ts' THEN 'typescript'
    WHEN path LIKE '%.js' OR path LIKE '%.jsx' OR path LIKE '%.mjs' OR path LIKE '%.cjs' THEN 'javascript'
    WHEN path LIKE '%.py' THEN 'python'
    ELSE 'markdown' END;

-- Machine tags: derived from code on every reindex (tags.rs). Human judgement
-- lives in records, so nothing here is authoritative, and tags never feed ranking.
CREATE TABLE tags (
  id           INTEGER PRIMARY KEY,
  chunk_id     INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('constant','type','config','boundary','check','api','ceiling')),
  name         TEXT NOT NULL,
  value        TEXT,
  start_offset INTEGER NOT NULL,
  end_offset   INTEGER NOT NULL
);
CREATE INDEX tags_kind ON tags(kind, name);
CREATE INDEX tags_chunk ON tags(chunk_id);
