import { BACKOFF_CAP_MINUTES, BACKOFF_START_SECONDS, MAX_SYNC_ATTEMPTS } from "../config";
import { open } from "./sqlite";

export type Mutation = { id: string; entity: "spot" | "alert" | "session"; field: string; value: unknown; attempts: number };

export class SyncQueue {
  private db = open("sync-queue.db");

  enqueue(m: Mutation) {
    this.db.execute("INSERT INTO queue(id, body) VALUES (?, ?)", [m.id, JSON.stringify(m)]);
  }

  // TODO: per-entity queues once alerts sync separately
  nextDelaySeconds(m: Mutation): number | null {
    if (m.attempts >= MAX_SYNC_ATTEMPTS) return null;
    return Math.min(BACKOFF_START_SECONDS * 2 ** m.attempts, BACKOFF_CAP_MINUTES * 60);
  }
}
