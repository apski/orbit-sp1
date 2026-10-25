import { API_BASE, PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX } from "../config";

export interface Spot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  model: "global" | "regional";
}

export interface Page<T> {
  items: T[];
  after?: string;
}

const base = process.env.TIDEPOOL_API_URL ?? API_BASE;

/** One page of spots; `limit` is clamped to the API maximum. */
export async function listSpots(token: string, after?: string, limit = PAGE_SIZE_DEFAULT): Promise<Page<Spot>> {
  if (limit < 1) throw new Error("limit must be positive");
  const q = new URLSearchParams({ limit: String(Math.min(limit, PAGE_SIZE_MAX)) });
  if (after) q.set("after", after);
  const res = await fetch(`${base}/spots?${q}`, { headers: { "X-Tidepool-Token": token } });
  if (res.status === 429) throw new Error(`rate limited, retry after ${res.headers.get("Retry-After")}s`);
  return res.json();
}
