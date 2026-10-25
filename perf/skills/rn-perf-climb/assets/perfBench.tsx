// Level 1 for perf-climb: mount one React Native component under React.Profiler in Jest. There's no
// simulator, Metro or flows. Copy to perf/bench/perfBench.tsx and write benches as
// perf/bench/*.perf.test.tsx. perf.mjs runs the cmd once per bench with PERF_REPS / PERF_WARMUP and
// parses the METRIC lines.
// Trust the counts (commits, render counts, requests). The ms are V8 under Jest's dev React, not
// Hermes on a device.
import { Profiler, type ReactElement } from 'react';
import { act, render, type RenderResult } from '@testing-library/react-native';

const out = (line: string) => process.stdout.write(`${line}\n`); // console.* is decorated by Jest

export async function benchComponent(
  name: string,
  element: () => ReactElement,
  { settleMs = 60, interact }: { settleMs?: number; interact?: (r: RenderResult) => Promise<void> } = {},
) {
  const reps = Number(process.env.PERF_REPS ?? 7), warmup = Number(process.env.PERF_WARMUP ?? 1);
  out('META debug=true');
  for (let i = 0; i < warmup + reps; i++) {
    const perf = (globalThis as any).__PERF__;
    perf?.reset();
    let commits = 0, renderMs = 0;
    const onRender = (_id: string, _phase: string, actual: number) => { commits++; renderMs += actual; };
    const t0 = performance.now();
    const r = await render(<Profiler id={name} onRender={onRender}>{element()}</Profiler>);
    await act(() => new Promise((res) => setTimeout(res, settleMs))); // effects, fetches, first list batches
    if (interact) await interact(r);
    const ms = performance.now() - t0;
    await r.unmount();
    if (i < warmup) continue;
    out(`METRIC ${name}_ms=${ms.toFixed(2)}`);
    out(`METRIC react:commits=${commits}`);
    out(`METRIC react:render_ms=${renderMs.toFixed(2)}`);
    for (const [k, v] of Object.entries(perf?.counts ?? {}))
      out(k.startsWith('fixture:') ? `FIXTURE ${k.slice(8)}=${v}` : `METRIC count:${k}=${v}`);
  }
}
