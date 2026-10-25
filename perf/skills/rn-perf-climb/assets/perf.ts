// rn-perf-climb marks. Copy into the app (e.g. src/perf.ts) and import it FIRST in the entry file.
// `perf.mjs bench` reads globalThis.__PERF__ over the Hermes debugger after every run.
// Cost is a push into an array; safe to ship. Marks are capped so a long session can't grow it.

type Perf = {
  epoch0: number; // Date.now() at performance.now() == 0, maps host time (@launch) onto mark time
  dev: boolean;
  marks: { n: string; t: number }[];
  counts: Record<string, number>;
  reset(): void;
};

const g = globalThis as any;

const P: Perf = (g.__PERF__ ??= {
  epoch0: Date.now() - performance.now(),
  dev: __DEV__,
  marks: [],
  counts: {},
  reset() {
    this.marks = [];
    this.counts = {};
  },
});

export const mark = (n: string) => {
  if (P.marks.length > 2000) P.marks.shift();
  P.marks.push({ n, t: performance.now() });
};

export const count = (n: string, by = 1) => {
  P.counts[n] = (P.counts[n] ?? 0) + by;
};

// Every request counted as net:<METHOD> <path with ids collapsed>, e.g. net:GET /chats/:id/messages.
// This is what catches "reload fetches the chat twice".
if (!g.__PERF_FETCH__) {
  g.__PERF_FETCH__ = true;
  const realFetch = g.fetch;
  g.fetch = (input: any, init?: any) => {
    try {
      const url = typeof input === 'string' ? input : input?.url ?? String(input);
      const path = url.replace(/^\w+:\/\/[^/]+/, '').split('?')[0];
      const method = (init?.method ?? input?.method ?? 'GET').toUpperCase();
      count(`net:${method} ${path.replace(/\/[0-9a-f-]{6,}|\/\d+/gi, '/:id')}`);
    } catch {}
    return realFetch(input, init);
  };
}

mark('js:start');
