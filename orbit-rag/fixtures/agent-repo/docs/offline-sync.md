# Offline sync

## Storage

Pending mutations are written to a sync queue persisted in SQLite (WAL mode) before the UI updates optimistically. The queue survives app termination and device restarts.

## Retries

Failed uploads are retried with exponential backoff starting at 2 seconds and doubling up to a cap of 5 minutes. A mutation is abandoned after 8 attempts and surfaced to the user in the sync status sheet.

## Conflicts

Conflict resolution is last-writer-wins per field, using the server timestamp as the authority. Deleting a spot wins over any concurrent edit of that spot.

## Background execution

On iOS the queue drains in a `BGProcessingTask`; on Android in a WorkManager job with a network constraint. Neither blocks the main thread.

## Map tiles

Offline map tiles are cached up to 200 MB; tiles for the least recently viewed spots are evicted first.
