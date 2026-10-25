// Level 2 for perf-climb: test-only routes that mount one subsystem with fixture data in the real app,
// skipping startup, navigation and network. `perf.mjs bench` calls globalThis.__perfRoute('chat?n=3000')
// over the debugger. Deep links work too: myapp://perf/chat?n=3000, or in Expo Go
// exp://127.0.0.1:8081/--/perf/chat?n=3000. "blank" clears it between reps.
//
// Ships only when Metro runs with PERF_ROUTES=1. Add to metro.config.js:
//   if (process.env.PERF_ROUTES !== '1') {
//     const next = config.resolver.resolveRequest;
//     config.resolver.resolveRequest = (ctx, name, platform) =>
//       /perfRoutes$/.test(name) ? { type: 'empty' } : (next ?? ctx.resolveRequest)(ctx, name, platform);
//   }
// Then `perf.mjs leakcheck <exported bundle dir>` must report clean for a release export.
// App wiring:
//   import { PerfRouteHost } from './src/perfRoutes';
//   const Host = PerfRouteHost ?? Fragment; ... <Host><App/></Host>   (the stub exports nothing)
import { type ReactNode, useEffect, useState } from 'react';
import { Linking, View } from 'react-native';
import { mark } from './perf';

(globalThis as any).PERF_ROUTES_ENABLED = true; // leakcheck marker; property names survive minification

type Q = Record<string, string>;
const ROUTES: Record<string, (q: Q) => ReactNode> = {
  // chat: (q) => <ChatList msgs={messages(Number(q.n ?? 3000))} />,   <- your subsystems
};

export function PerfRouteHost({ children, routes = ROUTES }: { children: ReactNode; routes?: typeof ROUTES }) {
  const [route, setRoute] = useState<{ path: string; q: Q; key: number } | null>(null);
  useEffect(() => {
    const open = (url: string | null) => {
      const m = url?.match(/perf\/([\w-]+)(?:\?(.*))?$/);
      if (!m) return;
      mark('route:open');
      const q: Q = Object.fromEntries((m[2] ?? '').split('&').filter(Boolean).map((kv) => kv.split('=').map(decodeURIComponent)));
      setRoute({ path: m[1], q, key: Date.now() });
    };
    Linking.getInitialURL().then(open);
    const sub = Linking.addEventListener('url', (e) => open(e.url));
    (globalThis as any).__perfRoute = (r: string) => open(`perf/${r}`);
    return () => { sub.remove(); delete (globalThis as any).__perfRoute; };
  }, []);
  if (!route) return <>{children}</>;
  const make = routes[route.path];
  return <View key={route.key} style={{ flex: 1, backgroundColor: 'white' }}>{make?.(route.q)}</View>;
}
