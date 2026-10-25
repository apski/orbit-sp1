# Lab designs for the next subsystems

Each design gives: the hypothesis, knobs (doses), the ruler (signpost start → native end point), the native twin, the
mechanism to read in source, scanner and probe ideas, and the outside judge. Reuse NavLab's harness (sweep, analyze,
model, journey, hygiene) and replace only these parts.

## Lists (FlatList / FlashList / SectionList vs UICollectionView)
- Hypothesis: scroll hitches and mount cost grow with row complexity × virtualization settings, not row count.
- Knobs: rows, views per row, row height variance, `initialNumToRender`/`windowSize`/FlashList `estimatedItemSize`, images per row, re-render on scroll (unstable `renderItem`).
- Ruler: interval `listmount` (mount → first rows committed, ended by a native probe in the last visible row); scroll = hitch events per second during a fixed-velocity scripted scroll (CADisplayLink); blank-area counter.
- Twin: UICollectionView compositional layout with cell reuse (not UIStackView).
- Mechanism: VirtualizedList windowing and batch scheduling (`VirtualizedList.js`), FlashList recycling.
- Scanner: list components with inline `renderItem`, no `keyExtractor`, `.map` in ScrollView, missing `getItemLayout`. Probe: `count('render:<Row>')`.
- Judge: hitch time ratio (Apple's metric) on device; held-out row designs.

## Startup (cold launch → first interactive frame)
- Hypothesis: JS bundle evaluation and eager imports dominate RN cold start; native init and first-frame layout are second.
- Knobs: bundle size (synthetic modules), eager vs `inlineRequires`/lazy imports, number of native modules initialised, providers at the root, initial-screen weight, Hermes bytecode on/off.
- Ruler: `process start` (sysctl) → `js:start` → `app:interactive` (native probe on first real content) → first scroll-ready frame. The xctrace App Launch template for pre-main.
- Twin: a native app with the same first screen; the baseline is pre-main + first frame.
- Mechanism: the Metro bundle, `InitializeCore`, TurboModule lazy init, Hermes bytecode loading.
- Scanner: import graph from the entry (eager fan-out); providers at the root. Probe: marks at entry, first commit, interactive.
- Judge: device cold launches (reboot, or terminate and wait), 10+ reps; App Store Connect launch metrics as field truth.

## Images
- Hypothesis: decode on the main thread and oversized sources cause the hitches; the cache policy causes re-decodes on return.
- Knobs: source size vs display size, format (AVIF/WebP/JPEG/PNG), count per screen, `expo-image` vs `Image` vs FastImage, cache hits.
- Ruler: image request → decoded → displayed (native probe on the image view's first non-empty layer contents); main-thread decode samples (ImageIO buckets).
- Twin: UIImageView with downsampling (`CGImageSourceCreateThumbnailAtIndex`).
- Scanner: images without explicit size, remote URLs without resizing params. Probe: count decodes and bytes per screen.
- Judge: hitches + memory peak on device; held-out image sets.

## State management (re-render storms)
- Hypothesis: store or context updates re-render far more components than the change touches; cost scales with subscribers × update frequency.
- Knobs: subscriber count, selector granularity, update frequency, store type (context / Redux / Zustand / MobX / Jotai), React Compiler on/off.
- Ruler: dispatch → commit end (React Profiler in a profiling build, or JS marks + native probe on the affected view); renders per update (counter).
- Twin: native observation (Observation framework / Combine) updating the same UI.
- Mechanism: context propagation, `useSyncExternalStore` selector equality, MobX reaction scheduling.
- Scanner: providers above navigators, `useSelector` returning new objects, context values built inline. Probe: `navprobeReduxMiddleware`, zustand subscribe, MobX spy.
- Judge: renders-per-update counts (deterministic) plus held-out stores.

## Animations and gestures
- Hypothesis: JS-driven animations hitch whenever the JS thread is busy; UI-thread (Reanimated worklets / native driver) ones don't. The cost is JS work during the animation.
- Knobs: driver (JS / native driver / Reanimated), concurrent JS load (synthetic busy loop during the animation), number of animated views, layout animations.
- Ruler: hitch events and frame pacing during the animation window; the JS-thread busy fraction.
- Twin: Core Animation / UIViewPropertyAnimator.
- Scanner: `Animated` without `useNativeDriver`, layout-affecting animated props, gesture handlers on the JS thread. Probe: `span` around heavy JS during transitions.
- Judge: hitch time ratio on a 120Hz device.

## Native modules / JSI / bridging
- Hypothesis: per-call overhead is negligible with JSI/TurboModules but large payload serialization and sync calls on the JS thread are not.
- Knobs: calls per frame, payload size, sync vs async, TurboModule vs legacy bridge vs Expo module.
- Ruler: JS call → native entry → return (signposts on both sides of one module).
- Twin: a direct Swift call (the floor).
- Judge: microbenchmark A/A floors, then real call sites through journeys.

## Networking
- Hypothesis: screen-open latency is dominated by request waterfalls and double fetches, not by JS processing.
- Knobs: requests per screen, serial vs parallel, cache hit, payload size and JSON parse cost.
- Ruler: screen press → data ready → content on screen; `count('net:*')`.
- Scanner: fetch-in-effect patterns with unstable deps; query keys. Probe: `instrumentFetch`/`instrumentAxios`, React Query cache events.
- Judge: request counts (deterministic) and a local mock server with fixed latency.

## Brownfield / coexistence (RN inside native and native inside RN)
- Hypothesis: the cost lives at the boundaries. First RN entry (runtime start, bundle load), each RN root view mount, cross-boundary navigation and shared state.
- Knobs: number of RN root views (one host vs many), pre-warmed runtime vs lazy, native↔RN screen alternation in one flow, shared-state sync frequency.
- Ruler: native push → RN root content on screen; RN → native push; first-entry vs warm-entry.
- Twin: an all-native flow with the same screens.
- Mechanism: `RCTRootView`/surface creation, the Fabric surface lifecycle, react-native-screens inside UINavigationController.
- Scanner: map native entry points into RN (module names registered, presented hosts). Probe: signposts at host creation and first commit.
- Judge: device first-entry runs after a cold launch; held-out flows.
- Decision use: tells you whether "one RN host for many screens" or "native shell + RN screens" is cheaper, and where a migration seam should go.
