-- ANN lookups go from embedding ids to the items that use them, and the vector
-- retriever counts a model's vectors on every query: index both directions.
CREATE INDEX IF NOT EXISTS embeddings_model_id ON embeddings(model, id);
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(hash);
CREATE INDEX IF NOT EXISTS record_index_hash ON record_index(hash);
CREATE INDEX IF NOT EXISTS diff_hunks_hash ON diff_hunks(hash);
