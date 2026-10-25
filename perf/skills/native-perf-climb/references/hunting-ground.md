# Hunting ground: where SwiftUI / UIKit perf bugs hide

Each entry gives the symptom, the signal that proves it (counter or profile), and the usual fix.
Prove the signal first.

## 1. Big screen opens slowly

| Cause | Signal | Fix |
|---|---|---|
| Eager container: `VStack`/`HStack` + `ForEach` over a large array inside `ScrollView` | `count:body:Row` ≈ total items, not visible items; a hang on open | `LazyVStack`/`List`, or `UICollectionView` with diffable data source |
| Formatter or regex built per row (`DateFormatter()`, `NumberFormatter()`, `NSRegularExpression`) | CPU in `-[NSDateFormatter init]`/ICU under row body; scales with rows built | `static let` formatter, or precompute strings when data arrives |
| Heavy work in `body` (sorting, filtering, grouping, Markdown/AttributedString parse) | Body count is fine but each one is slow; `function_callers` hits sort/parse on main | Move it into the model, cache by id, compute off-main |
| Synchronous decode or IO on main (`Data(contentsOf:)`, big JSON, Core Data fetch on viewContext) | Main-thread hang with Foundation/CoreData frames | `Task.detached`/background context, page the fetch |
| Images decoded at full size on main | CPU in ImageIO / `CGImage` decode, hitches as cells appear | Downsample (`CGImageSourceCreateThumbnailAtIndex`), `preparingThumbnail(of:)`, async loading |
| Self-sizing measured repeatedly | `count:layout:*` high, many layout passes | Fixed or estimated sizes, `UICollectionViewCompositionalLayout` with estimates, avoid `GeometryReader` in rows |

## 2. Too many SwiftUI updates

| Cause | Signal | Fix |
|---|---|---|
| `ObservableObject` with broad `@Published`: any change invalidates every observer | Body counts of unrelated views go up together | `@Observable` (per-property tracking), or split the objects |
| Unstable identity: `ForEach(items, id: \.self)` on mutable values, `UUID()` in `id`, `.id(UUID())` | Rows rebuild and lose state on every update; counts ≈ total × updates | Stable model ids |
| `AnyView` or deep conditional `if/else` swapping view types | Subtree teardown and rebuild in the SwiftUI Instruments template | `@ViewBuilder`, keep types stable, hide with `.opacity` |
| Closures or objects created in `body` passed down (non-Equatable props) | Child bodies re-run on every parent update | Make props Equatable, `EquatableView`, lift the closures |
| Timer or `onReceive` publishing at high frequency into shared state | Steady body counts while idle | Scope state to the leaf view, throttle |

## 3. Switching back is slow (`<span>#2`)

| Cause | Signal | Fix |
|---|---|---|
| Destination rebuilt on every push (state lives in the destination view) | `#2` ≈ first open, and fetch count goes up on return | Hoist state to a model that outlives the view, cache results |
| `.task`/`onAppear` refetch with no cache | `count:net:*` goes up on each appear | Cache with a freshness check, revalidate in the background |
| `onAppear` **and** `.task` both loading | `count:net:*` = 2 on first open | Pick one; `.task` is cancellable |

## 4. Scrolling hitches and jumps

| Cause | Signal | Fix |
|---|---|---|
| Expensive cell configuration | `count:hitch:ms` rises with scroll distance; CPU in `cellForRowAt` | Precompute view models, reuse formatters, prefetch (`UICollectionViewDataSourcePrefetching`) |
| Offscreen rendering: shadows without `shadowPath`, `cornerRadius`+`masksToBounds` on many layers, blur | Render-server time, hitches with low main-thread CPU | `shadowPath`, pre-rounded images, fewer blurs |
| Prepending older items without anchoring the offset | `count:scroll:jump` > 0 on load-older | `UICollectionView` diffable snapshot plus offset correction, or `scrollPosition(id:)` / `defaultScrollAnchor(.bottom)` (iOS 17+) |
| Heights change after async content arrives | Jumps and layout counts after appear | Reserve size, use placeholders with fixed aspect ratios |

## 5. Startup (`process:start` → `app:interactive`)

| Cause | Signal | Fix |
|---|---|---|
| Work in `App.init`/`didFinishLaunching` (SDK inits, migrations, Keychain, big UserDefaults reads) | Large gap before the first frame; App Launch template | Defer past first frame, lazy-init SDKs |
| Too many dynamic frameworks / static initializers | Pre-main time (App Launch template "dyld") | Merge or static-link frameworks, remove `+load` / static init work |
| First screen waits on the network | `count:net:*` before `app:interactive`; spinner first | Render cached state first, refresh after |
| Heavy root view graph built eagerly (all tabs) | Body counts for tabs not yet visible | Lazy tab contents |

## 6. Older content loads slowly

| Cause | Signal | Fix |
|---|---|---|
| Fetch triggered only at the very end | `older:request` mark lags reaching the end | Trigger on the item N-from-end appearing, prefetch |
| Whole list replaced on append | Body/cell counts ≈ all rows per page | Append-only diffable snapshot, stable ids |
