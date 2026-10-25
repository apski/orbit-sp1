-- The symbol a code chunk defines (last word of its last heading element:
-- "impl Store > fn reindex" -> reindex), indexed, so docs that name it in
-- backticks can point at the definition (coderefs.rs).
ALTER TABLE chunks ADD COLUMN symbol TEXT;
UPDATE chunks SET symbol = (
  SELECT substr(x, length(rtrim(x, replace(x, ' ', ''))) + 1)
  FROM (SELECT json_extract(chunks.heading_path, '$[#-1]') AS x)
) WHERE lang <> 'markdown' AND json_array_length(heading_path) > 0;
CREATE INDEX IF NOT EXISTS chunks_symbol ON chunks(symbol);
