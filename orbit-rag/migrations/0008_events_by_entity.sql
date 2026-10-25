-- Sync asks "was this source already found missing?" per source: index events by entity.
CREATE INDEX IF NOT EXISTS events_entity ON events(entity, id);
