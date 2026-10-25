-- Stable integer ids for embeddings, so an ANN index can key on them: without an
-- explicit INTEGER PRIMARY KEY, VACUUM may renumber rowids.
CREATE TABLE embeddings_new (
  id    INTEGER PRIMARY KEY,
  hash  TEXT NOT NULL,
  model TEXT NOT NULL,                        -- provider/model@version
  vec   BLOB NOT NULL,                        -- f32 LE, L2-normalised
  UNIQUE (hash, model)
);
INSERT INTO embeddings_new (hash, model, vec) SELECT hash, model, vec FROM embeddings ORDER BY rowid;
DROP TABLE embeddings;
ALTER TABLE embeddings_new RENAME TO embeddings;
