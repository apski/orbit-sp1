# React Native (New Architecture)

## Threads

- **JS thread**: every line of your JS. React render and commit, Yoga layout for JS-driven
  commits, event handlers (`onPress`), JS-stack navigation, `Animated` without the native driver,
  timers, react-query/store updates, `JSON.parse`, sync TurboModule calls.
- **UI (main) thread**: mounting native views, touch delivery, native-stack (react-native-screens)
  transitions and edge-swipe, ScrollView scrolling, `useNativeDriver` animations, Reanimated
  worklets on the UI runtime, Gesture Handler recognizers.
- **Native module queues**: async TurboModule/legacy methods run on the module's queue (its own
  serial queue unless it returns the main queue). Hermes GC is mostly concurrent.

A response is native-first only if nothing on its path needs the JS thread. Checklist per response:
who handles the touch, who decides to navigate, who animates, who renders the destination.

## Find

- **Stall test** (the decisive one): a debug-only knob that busy-loops JS for 3 s
  (`const t = Date.now(); while (Date.now() - t < 3000) {}`), then trigger the response. If it
  waits out the stall, it's on JS. NavCore: "Block JS 3s" in `Activity.tsx`, `flows/js-busy.mjs`.
- **Lag probe** (cheap, works in Release): log the largest gap per second between interval ticks.

```ts
let last = performance.now(), worst = 0;
setInterval(() => { const n = performance.now(); worst = Math.max(worst, n - last - 50); last = n; }, 50);
setInterval(() => { if (worst > 50) console.log(`[lag] JS blocked ${worst.toFixed(0)}ms`); worst = 0; }, 1000);
```

- **Profile**: argent `react-profiler-start/stop` + `react-profiler-cpu-summary` (Hermes sampling
  CPU + React commits), `profiler-combined-report` with `native-profiler-*` to see both threads.
  React Native DevTools (Performance panel in recent RN). Dev menu Perf Monitor shows UI vs JS FPS
  (a JS FPS drop with steady UI FPS = JS-bound).
- **Startup JS**: pass the kernel process start (epoch ms, from `sysctl` `kp_proc.p_un.__p_starttime`)
  from native as an initial prop, then log `Date.now() - processStart` at the top of `index.js`,
  after imports, and at the first commit. The gaps are module init and first render. NavCore's
  `processStart` prop does this.
  NavCore's `jsInitMs` knob simulates start-up work to see what waits for it.
- **Grep candidates** (then confirm in the profile):

```bash
rg -n -g '*.{ts,tsx,js,jsx}' -g '!node_modules' 'JSON\.(parse|stringify)|\.sort\(|AsyncStorage\.(getItem|multiGet)|useNativeDriver:\s*false|onScroll=|InteractionManager|createStackNavigator|PanResponder|new Array\(\d{4,}'
rg -n -g '*.{ts,tsx,js}' -g '!node_modules' '^(const|let) .*= (new |\w+\()' . | head -50   # top-level work at import time
```

## Move: by what waits

**Navigation**: `@react-navigation/native-stack` (not the JS `stack`), so transitions and
edge-swipe run natively. For pushes that must ignore a busy JS thread, own the navigation
natively (native-first host: native screens + RN flows as surfaces). NavCore D5 is the evidence.

**Touch feedback, gestures, drag, scroll-linked UI**: Gesture Handler (native recognizers) +
Reanimated worklets (`useAnimatedStyle`, `useAnimatedScrollHandler`). `Animated` with
`useNativeDriver: true` for opacity/transform. Call back to JS (`runOnJS`/`scheduleOnRN`) only at
the end of the gesture, not per frame.

**Lists**: FlashList or FlatList with `getItemLayout`, memoized rows, stable `keyExtractor`, small
`windowSize` for heavy rows. Blank cells while scrolling = JS can't render new rows fast enough.

**Rendering that must happen but isn't urgent**: `startTransition` / `useDeferredValue`, so a tap's
urgent update commits first and the heavy render can be interrupted. Show the destination shell,
then fill it. `requestIdleCallback` for post-interaction work (`InteractionManager` is deprecated
in recent RN).

**Pure computation >50 ms**, in order of preference:
1. **Don't do it on the device's JS**: ask the server for the sorted/filtered/paged shape.
2. **Native module** (TurboModule or Nitro, implemented in Swift/Kotlin on a background queue,
   returns a Promise). Return the small result. Existing native libs cover common cases:
   `react-native-quick-crypto`, op-sqlite/MMKV (query in native rather than parse a blob in JS),
   native image resizing.
3. **Background worklet runtime** (`createWorkletRuntime` + `runOnRuntime` from
   react-native-worklets/Reanimated; names vary by version, check the installed one). It can't see
   the main JS heap: arguments and results are copied, so it suits self-contained CPU work, not
   work over your store.
4. **Chunk and yield** on the JS thread when 1–3 don't fit:

```ts
async function inChunks<T>(items: T[], size: number, fn: (x: T) => void) {
  for (let i = 0; i < items.length; i += size) {
    items.slice(i, i + size).forEach(fn);
    await new Promise(r => setTimeout(r, 0));   // let touches and renders in
  }
}
```

Keep each chunk under ~8 ms. The total gets longer, but a tap waits at most one chunk.

**Big JSON / storage at screen open**: `JSON.parse` is synchronous and builds JS objects on the JS
thread wherever the bytes are parsed. Store smaller pieces, page the cache, keep the hot slice
small, or query natively (SQLite/MMKV per key) instead of parsing one blob.

**Startup**:
- `inlineRequires: true` in `metro.config.js` (check it is on), and lazy `require`/`import()` for
  screens and heavy libs not on the first screen.
- Defer store hydration, i18n of other locales, analytics/SDK init, feature-flag fetches until
  after the first commit (`requestIdleCallback` or the first screen's effect).
- No heavy top-level work in modules (big constant tables, regex compilation, sync storage reads).
- Hermes bytecode (default). Measure bundle size before/after.
- If the first screen still waits for JS: make it native (NavCore layout B, native-first host), so
  launch time no longer depends on JS start-up.

**Native modules at launch**: `requiresMainQueueSetup` returning YES blocks main during start-up.
Prefer lazy TurboModules. Avoid sync TurboModule methods that `dispatch_sync` to main.

## Prove

- Stall test again: the response no longer waits out the 3 s stall.
- Lag probe: worst gap under the budget during the flow.
- Response metric from native ("on screen" marker at the next display frame, as NavCore's
  `DataMarker`), not the JS commit time. Release, device, median of several runs. `rn-perf-climb`
  for the harness.

## Gotchas

- `async`/Promises don't create threads. Everything before the first real `await` runs
  synchronously, and the continuation runs on the JS thread too.
- A heavy worklet moves the problem to the UI thread: it now hitches scrolling and gestures. Keep
  UI-runtime worklets within the frame budget.
- `useNativeDriver` supports only non-layout props (opacity, transform). Animating width/height
  goes through JS or Reanimated layout animations.
- Dev mode JS is ~3× slower and runs extra checks. Never report Debug numbers.
- A dismissed RN surface can stay in the AX tree briefly. Time from native markers, not from AX
  polling.
