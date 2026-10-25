-- Document kind per chunk (kinds.rs: decision, spec, doc, code, history, agent,
-- marketing), so ranking can weigh a decision above a troubleshooting checklist.
-- Filled by the reindex that runs once when a store reaches this version.
ALTER TABLE chunks ADD COLUMN kind TEXT NOT NULL DEFAULT 'doc';
CREATE INDEX IF NOT EXISTS chunks_kind ON chunks(kind);
