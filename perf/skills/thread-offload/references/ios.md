# iOS native (SwiftUI / UIKit)

## Threads

- **Main thread**: UIKit/SwiftUI event handling, `body` evaluation, layout, Core Animation
  transaction commit, scroll tracking and deceleration. Blocked main = no taps, no scroll, frozen
  frame.
- **Render server** (out of process): compositing and committed CA animations. A
  `UIView.animate`/push animation already in flight keeps going during a short main stall. A new
  one can't start.
- **Everything else**: Swift concurrency's cooperative pool (one thread per core), GCD queues,
  `URLSession` delegate queues, Core Data private contexts.

## Isolation: where does my code actually run?

Check the target first. It changes the answer:

```bash
grep -E "SWIFT_DEFAULT_ACTOR_ISOLATION|SWIFT_APPROACHABLE_CONCURRENCY|SWIFT_UPCOMING_FEATURE|SWIFT_VERSION" \
  *.xcodeproj/project.pbxproj | sort -u
```

| Code | Runs on |
|---|---|
| `@MainActor` type/func, any `View`, `UIViewController`, (default isolation = MainActor) un-annotated code | main |
| `Task { … }` | the caller's actor. From main, that's main |
| `Task.detached { … }` | the pool |
| `nonisolated func f() async` | Swift ≤6.1 / feature off: the pool. Approachable Concurrency (SE-0461) on: **the caller's actor** |
| `@concurrent func f() async` (Swift 6.2+) | the pool, always |
| `actor` method | that actor's executor (off main) |
| sync function called from anywhere | the caller's thread. `await` doesn't move a sync call |

`await` suspends without blocking main, but main is only free while the work it awaits runs
elsewhere.

## Find

- **Instruments**: Time Profiler with "Hangs" (or the Hangs instrument), filter the main thread,
  invert the call tree. System Trace when main is *waiting* (blocked on a lock, a semaphore, I/O)
  rather than computing.
- **Field**: Xcode Organizer → Hangs; MetricKit `MXHangDiagnostic`; `os_signpost` intervals around
  suspects (show up in Points of Interest).
- **Debug**: Thread Performance Checker (scheme → Diagnostics) flags priority inversions and
  non-UI work on main.
- **Startup**: the App Launch template; `DYLD_PRINT_STATISTICS` is gone on recent iOS, so use the
  template. Look at `App.init`, `application(_:didFinishLaunchingWithOptions:)`, static
  initializers, singletons created on first access during launch.
- **Grep candidates** (then confirm in the profile):

```bash
rg -n --type swift 'Data\(contentsOf|JSONDecoder\(\)\.decode|JSONSerialization|PropertyListDecoder|viewContext\.(fetch|count)|UIImage\((contentsOfFile|data)|CGImageSource|\.sorted\(|\.filter\(|NSRegularExpression|try Regex|DispatchQueue\.main\.sync|\.wait\(\)|DateFormatter\(\)|NumberFormatter\(\)|CryptoKit|CC_SHA|String\(contentsOf'
```

## Move

**Pure computation / decoding** (Swift 6.2+):

```swift
@concurrent
func decodeItems(_ data: Data) async throws -> [Item] {   // Item: Sendable
    dispatchPrecondition(condition: .notOnQueue(.main))
    return try JSONDecoder().decode([Item].self, from: data)
}

// in a @MainActor model
items = try await decodeItems(data)
```

Older toolchains: `nonisolated func … async` (feature off) or
`await Task.detached(priority: .userInitiated) { … }.value`. GCD
(`DispatchQueue.global(qos: .userInitiated).async { …; DispatchQueue.main.async { … } }`) is fine in
UIKit code that doesn't use Swift concurrency.

**Images**: decode off main. `await image.byPreparingThumbnail(ofSize:)` for downscaling,
`image.preparingForDisplay()` inside a background task, or `CGImageSourceCreateThumbnailAtIndex`
for large files. Cache the decoded result, not the `Data`.

**Persistence**: Core Data `container.performBackgroundTask { ctx in … }` or a
`newBackgroundContext()`, pass `NSManagedObjectID`s back. SwiftData `@ModelActor` for heavy fetches
and imports, pass `PersistentIdentifier`s back. Keep `viewContext`/main `ModelContext` for what the
visible screen reads.

**Files, crypto, regex over large text, sort/filter of big arrays**: same `@concurrent` pattern.
Return what the screen needs (a page, a summary), not the whole set.

**Must stay on main**: view/layer mutation, `@Observable`/`ObservableObject` properties views read,
`NSAttributedString(data:… .html)` (uses WebKit, main only), UIKit APIs generally. Make these
short instead:
- SwiftUI: keep `body` cheap. Compute derived values in the model off main and store them. Split
  views so a change re-evaluates a small `body`; `@Observable` tracks per property read. `Lazy*`
  stacks/`List` for long content.
- UIKit: cell configuration only assigns precomputed values; `prefetchDataSource` starts decoding
  ahead; diffable snapshots built off main can be applied with `apply(_:animatingDifferences:)`
  (Apple documents calling it from a background queue consistently).

**Startup**: move SDK init, migrations, cache warming and analytics after the first frame (a
`Task(priority: .utility)` started from the first screen's `.task`/`viewDidAppear`). Avoid work in
`+load`/static initializers. Fewer dynamic frameworks (merge or make static).

**Formatters**: `DateFormatter`/`NumberFormatter` creation is expensive. Make one static instance,
or use `FormatStyle` (`date.formatted(...)`), which caches.

## Prove

- Same Instruments template before/after. The function is gone from the main thread's heaviest
  stack, and the hang/hitch count for the flow dropped.
- `native-perf-climb` counters: `main:uninstrumented`, `count:hitch`, span for the response.
- Keep the `dispatchPrecondition` in the moved function.

## Gotchas

- Results crossing actors must be `Sendable`. Value types of value types are free. Classes need
  `final class … : Sendable` with immutable state, or keep them inside one actor.
- `Task.detached` drops priority and task-locals; give it a priority explicitly.
- Hopping to main once per item (`await MainActor.run` in a loop) floods main. Batch, hop once.
- A `DispatchSemaphore` waited on by a high-priority thread for work at lower QoS is a priority
  inversion. Thread Performance Checker flags it. Use `async`/`await` instead.
- Simulator times reflect the Mac. Confirm on a device in Release before reporting.
