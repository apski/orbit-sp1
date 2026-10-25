// navprobe: runtime companion to navscan. Copy into the app (the install skill does) and attach it to the
// NavigationContainer ref. Safe in Release: a few array pushes per navigation action, capped.
//
//   import { attachNavProbe } from './navprobe';
//   <NavigationContainer ref={navRef} onReady={() => attachNavProbe(navRef, { model, signposts, journeys })}>
//
// Reads: globalThis.__NAVPROBE__.summary() over the Hermes debugger (argent debugger-evaluate), or Instruments
// signposts when `signposts` is the Lab native module (navaction intervals + navstate events, subsystem "lab").
// Journeys: launch with `-navlabJourney <name>` (iOS NSUserDefaults argument domain) to run a scripted sequence of
// navigation actions with no taps, then `signposts.exit()` ends an `xctrace record --launch` run.

type NavState = { type?: string; index?: number; routes: { name: string; key: string; state?: NavState }[] };
type Ref = {
  getRootState(): NavState;
  addListener(type: string, cb: (e: any) => void): () => void;
  dispatch(action: any): void;
  navigate(...args: any[]): void;
  goBack(): void;
  canGoBack(): boolean;
  isReady(): boolean;
};
type Coef = { base: number; per_navigator: number; per_tab_route: number; per_stack_screen: number };
export type NavModel = { arms: { rn: { coef: { js: Coef; render: Coef } }; rn_navaction?: { coef: { js: Coef } }; native?: { coef: { render: Coef } }; native_frames?: { coef: { render: Coef } } } };
export type Signposts = {
  begin(name: string, msg: string): number;
  end(handle: number, msg: string): void;
  event(name: string, msg: string): void;
  exit?(): void;
};
export type JourneyStep =
  | { navigate: string; params?: object }
  | { push: string; params?: object }
  | { goBack: true }
  | { popToTop: true }
  | { wait: number };
export type Shape = { path: string; navigators: number; tabRoutes: number; stackLen: number; totalRoutes: number };

const g = globalThis as any;
const now = () => (g.performance?.now ? g.performance.now() : Date.now());

const P = (g.__NAVPROBE__ ??= {
  actions: [] as any[],
  counts: {} as Record<string, number>,
  marks: [] as { n: string; t: number }[],
  max: { navigators: 0, tabRoutes: 0, stackLen: 0, totalRoutes: 0 },
  model: null as NavModel | null,
  summary() {
    const a = this.actions as any[];
    const worst = [...a].sort((x, y) => y.ms - x.ms).slice(0, 10);
    return { actions: a.length, max: this.max, counts: this.counts, worst, shapeNow: this.shapeNow?.() };
  },
});

/** Shape of the focused path: what every navigation action re-processes (see AGENTS.md: the mechanism). */
export function shapeOf(state: NavState | undefined): Shape {
  let s = state, navigators = 0, tabRoutes = 0, stackLen = 0;
  const parts: string[] = [];
  while (s && s.routes) {
    navigators++;
    const kind = s.type === 'tab' || s.type === 'drawer' ? s.type : 'stack';
    if (kind !== 'stack') tabRoutes += s.routes.length;
    else stackLen = s.routes.length;
    const r = s.routes[s.index ?? s.routes.length - 1];
    parts.push(`${kind}(${s.routes.length})>${r?.name}`);
    s = r?.state;
  }
  const total = (x?: NavState): number => (x?.routes ? x.routes.reduce((a, r) => a + 1 + total(r.state), 0) : 0);
  return { path: parts.join(' '), navigators, tabRoutes, stackLen, totalRoutes: total(state) };
}

const predict = (c: Coef | undefined, s: Shape) =>
  c ? c.base + c.per_navigator * s.navigators + c.per_tab_route * s.tabRoutes + c.per_stack_screen * s.stackLen : undefined;

/** Generic counters/marks for state, network and app code (see the install skill for where to call them). */
export const count = (name: string, by = 1) => void (P.counts[name] = (P.counts[name] ?? 0) + by);
export const mark = (name: string) => void (P.marks.length < 2000 && P.marks.push({ n: name, t: now() }));
let sp: Signposts | null = null;
export function span<T>(name: string, fn: () => T): T {
  const h = sp?.begin('span', name);
  const t = now();
  try { return fn(); } finally { count(`span:${name}:ms`, Math.round(now() - t)); if (h) sp!.end(h, ''); }
}

/** Count every request as net:<METHOD> <path with ids collapsed>. Catches waterfalls and double fetches. */
export function instrumentFetch() {
  if (g.__NAVPROBE_FETCH__ || !g.fetch) return;
  g.__NAVPROBE_FETCH__ = true;
  const real = g.fetch;
  g.fetch = (input: any, init?: any) => {
    try {
      const url = typeof input === 'string' ? input : input?.url ?? String(input);
      const p = url.replace(/^\w+:\/\/[^/]+/, '').split('?')[0].replace(/\/[0-9a-f-]{6,}|\/\d+/gi, '/:id');
      count(`net:${(init?.method ?? input?.method ?? 'GET').toUpperCase()} ${p}`);
    } catch {}
    return real(input, init);
  };
}

/** axios: instrumentAxios(apiClient) */
export function instrumentAxios(instance: any) {
  instance?.interceptors?.request?.use((cfg: any) => {
    count(`net:${String(cfg.method || 'get').toUpperCase()} ${String(cfg.url || '').split('?')[0].replace(/\/[0-9a-f-]{6,}|\/\d+/gi, '/:id')}`);
    return cfg;
  });
}

/** Redux: applyMiddleware(navprobeReduxMiddleware). Counts actions and time spent in reducers+subscribers. */
export const navprobeReduxMiddleware = () => (next: any) => (action: any) => {
  const t = now();
  const r = next(action);
  count(`redux:${action?.type ?? '?'}`);
  count('redux:ms', Math.round(now() - t));
  return r;
};

export function attachNavProbe(
  ref: Ref,
  opts: { model?: NavModel; signposts?: Signposts | null; journeys?: Record<string, JourneyStep[]>; settleMs?: number; journeyArg?: string } = {}
) {
  if (g.__NAVPROBE_ATTACHED__) return P;
  g.__NAVPROBE_ATTACHED__ = true;
  P.model = opts.model ?? null;
  sp = opts.signposts ?? null;
  P.shapeNow = () => shapeOf(ref.getRootState());

  let pending: { t: number; type: string; target?: string; h?: number } | null = null;
  ref.addListener('__unsafe_action__', (e: any) => {
    if (e?.data?.noop) return;
    const a = e?.data?.action ?? {};
    const target = a.payload?.name ?? a.payload?.screen;
    pending = { t: now(), type: a.type ?? '?', target, h: sp?.begin('navaction', `type=${a.type} target=${target ?? ''}`) };
  });
  ref.addListener('state', () => {
    const s = shapeOf(ref.getRootState());
    for (const k of ['navigators', 'tabRoutes', 'stackLen', 'totalRoutes'] as const) P.max[k] = Math.max(P.max[k], s[k]);
    if (!pending) return;
    const ms = now() - pending.t;
    const msg = `navigators=${s.navigators} tab_routes=${s.tabRoutes} stack_len=${s.stackLen} total_routes=${s.totalRoutes}`;
    if (pending.h) sp!.end(pending.h, msg);
    sp?.event('navstate', `${msg} path=${s.path.replace(/\s/g, '|')}`);
    if (P.actions.length < 1000)
      P.actions.push({ type: pending.type, target: pending.target, ms: Math.round(ms * 10) / 10, shape: s,
        predictedJsMs: predict((P.model?.arms.rn_navaction ?? P.model?.arms.rn)?.coef.js, s), predictedLagMs: (predict(P.model?.arms.rn.coef.render, s) ?? 0) - (predict((P.model?.arms.native_frames ?? P.model?.arms.native)?.coef.render, s) ?? 0) });
    pending = null;
  });

  // Journey mode: -navlabJourney <name> (Settings reads NSUserDefaults, which includes launch arguments on iOS)
  let name: string | undefined = opts.journeyArg;
  try { name ??= require('react-native').Settings?.get?.('navlabJourney'); } catch {}
  const steps = name && opts.journeys?.[name];
  if (steps) runJourney(ref, steps, opts.settleMs ?? 600).then(() => {
    sp?.event('done', `journey=${name} actions=${P.actions.length}`);
    setTimeout(() => sp?.exit?.(), 300);
  });
  return P;
}

/** Scripted navigation: each step waits for the resulting state change, then settles. */
export async function runJourney(ref: Ref, steps: JourneyStep[], settleMs = 600) {
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const nextState = () => new Promise<void>((res) => { const off = ref.addListener('state', () => { off(); res(); }); setTimeout(res, 5000); });
  while (!ref.isReady()) await sleep(50);
  await sleep(settleMs * 2);
  for (const s of steps) {
    if ('wait' in s) { await sleep(s.wait); continue; }
    const changed = nextState();
    if ('navigate' in s) ref.navigate(s.navigate as never, s.params as never);
    else if ('push' in s) ref.dispatch({ type: 'PUSH', payload: { name: s.push, params: s.params } });
    else if ('goBack' in s) { if (!ref.canGoBack()) continue; ref.goBack(); }
    else if ('popToTop' in s) ref.dispatch({ type: 'POP_TO_TOP' });
    await changed;
    await sleep(settleMs);
  }
}
