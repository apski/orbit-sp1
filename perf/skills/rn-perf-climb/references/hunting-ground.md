# Hunting ground: where RN perf bugs hide

Each entry gives the symptom, the profiler signal that proves it, and the usual fix. Prove the
signal before you fix anything.

## 1. Big screen opens slowly (first render)

| Cause | Signal | Fix |
|---|---|---|
| Too many rows mounted up front | `react.md` mount cascade: `Row ×60` when ~12 are visible | `initialNumToRender` ≈ one screen, sane `windowSize`, `maxToRenderPerBatch` |
| Heavy work in row render (markdown, date formatting, regex, hashing) | `profiler-cpu-query component_cpu Row` → hot function inside Row | Precompute when data arrives (once per message), or memoize by id |
| Whole-list transform on every render (sort, group-by-day, filter) | `call_tree` shows sort/reduce under the screen component, every commit | `useMemo` on `[messages]`, or do it in the data layer |
| Large JSON parse / storage read on the JS thread at open | CPU: `JSON.parse` / MMKV / AsyncStorage getter in the first commit window | Page the cache, parse lazily, keep the hot slice small |
| Variable-height measuring | Many `onLayout` commits after mount | FlashList with `estimatedItemSize`, or `getItemLayout` for fixed heights |
| Images decoding at full size | native.md: CPU in ImageIO / decode on main | Sized thumbnails, `expo-image` with `recyclingKey` |

## 2. Switching back is slow (`<span>#2`)

| Cause | Signal | Fix |
|---|---|---|
| Screen unmounts on blur and remounts | 2nd open has the same mount cascade as the 1st | Keep it mounted (navigator defaults, no `unmountOnBlur`, no changing `key`), `react-freeze`/`enableFreeze` |
| Refetch on focus with no cache | `count:net:*` goes up on return | Query cache `staleTime`, show cached data and revalidate in the background |
| Store change re-renders everything | Return commit lists unrelated screens and providers | Split contexts, selector-based subscriptions |

## 3. Scroll jumps and scroll work

Jump heuristic. Count it, don't eyeball it:
```ts
const last = useRef(0), dragging = useRef(false);
onScrollBeginDrag={() => (dragging.current = true)}
onMomentumScrollEnd={() => (dragging.current = false)}
onScroll={(e) => { const y = e.nativeEvent.contentOffset.y;
  if (!dragging.current && Math.abs(y - last.current) > 200) count('scroll:jump');
  last.current = y; }}
```

| Cause | Signal | Fix |
|---|---|---|
| Prepending older messages shifts content | `count:scroll:jump` > 0 on load-older | `maintainVisibleContentPosition={{ minIndexForVisible: 0 }}`, or an inverted list appending at the end |
| Index keys while prepending | Every row re-renders and remounts on page load | `keyExtractor={(m) => m.id}` |
| Rows change height after first layout (late images, async text) | Layout commits after mount, jump count rises | Reserve dimensions, fixed placeholders |
| All rows re-render on each append | `react:fiber_renders` ≈ total rows × pages | `memo(Row)`, stable `renderItem`, narrow `extraData` |
| Work in `onScroll` on the JS thread | CPU in the scroll handler during the scroll flow | `scrollEventThrottle`, Reanimated `useAnimatedScrollHandler` |

## 4. Fetching twice

| Cause | Signal | Fix |
|---|---|---|
| Effect deps not stable (object or array literal, inline function) | `count:net:X` ≥ 2, and grows with time on screen | Primitive deps, or hoist and memoize |
| Mount fetch plus a focus listener both fire | Exactly 2 on first open | Pick one owner, or let the cache dedupe |
| Two components fetch the same resource | 2 requests, different callers (`call_tree` on fetch) | Lift to one query key |
| Dev StrictMode double effect | 2 in dev, 1 with `--no-dev` | Not a bug. Leave it |

## 5. Startup

| Cause | Signal | Fix |
|---|---|---|
| Eager imports of the whole app | Big `js:start`→`app:interactive` gap with no network | `inlineRequires: true` in metro config, lazy screens |
| Sync storage hydrate of a large store | CPU in the persist/rehydrate parse before first render | Hydrate the minimum, defer the rest |
| Network waterfall before first paint | `count:net:*` serial before `app:interactive` | Render from cache first, parallelize |
| Fonts/splash gated on everything | Splash held until all promises resolve | Gate only on what the first screen needs |
| Native init work | native.md hangs or CPU on main before JS starts | Defer SDK init (analytics, crash reporting) past first frame |

## 6. Older content loads slowly (pagination)

| Cause | Signal | Fix |
|---|---|---|
| `onEndReached` fires late | `older:request` mark comes long after the user reaches the end | `onEndReachedThreshold` 0.5–1, prefetch the next page |
| Refetching the whole list instead of one page | `count:net` payload grows, `fixture` size jumps | Cursor pagination, merge pages |
| Merge creates new objects for old rows | All rows re-render on append | Keep row object identity, append-only merge |
