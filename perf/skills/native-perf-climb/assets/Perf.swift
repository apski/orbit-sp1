// perf-climb marks for SwiftUI/UIKit apps. Copy into the app target and call `Perf.start()` first
// thing at launch (App.init, or application(_:didFinishLaunchingWithOptions:)).
// `perf.mjs bench` reads Library/Caches/perf.jsonl from the simulator container after every run.
// Events carry host-epoch ms (the simulator shares the host clock), so the harness keeps only
// events at or after the moment it started the measured flow. Nothing needs resetting.
// Cost: a lock and an array append per call; a utility queue writes batches every 200ms.
// Spans and milestones (Perf.span / Perf.milestone / Perf.firstFrame) feed Orbit's `perf` report:
// journey -> screen -> phase attribution. Format: agents/orbit/schemas/trace-event.schema.json.

import Foundation
import QuartzCore
import os
#if canImport(UIKit)
import UIKit
#endif

enum Perf {
    private static let epoch0 = Date().timeIntervalSince1970 * 1000 - CACurrentMediaTime() * 1000
    private static let lock = NSLock()
    private static let queue = DispatchQueue(label: "perf.flush", qos: .utility)
    private static let url = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("perf.jsonl")
    nonisolated(unsafe) private static var buffer: [[String: Any]] = []
    nonisolated(unsafe) private static var counts: [String: Int] = [:]
    nonisolated(unsafe) private static var scheduled = false
    nonisolated(unsafe) private static var capturing = false

    /// Host-epoch milliseconds, monotonic within the process.
    static var now: Double { epoch0 + CACurrentMediaTime() * 1000 }

    /// A span endpoint, e.g. "chat:press" ... "chat:ready".
    static func mark(_ name: String) {
        #if canImport(UIKit)
        if Thread.isMainThread { MainActor.assumeIsolated { BusyMonitor.cut() } }
        #endif
        push(["k": "m", "n": name, "t": now])
    }

    /// Work counter: body evaluations, cell configs, requests. Summed per flush.
    /// In a SwiftUI body: `let _ = Perf.count("body:ChatRow")`.
    static func count(_ name: String, _ by: Int = 1) {
        lock.lock(); counts[name, default: 0] += by; lock.unlock()
        schedule()
    }

    /// Input size that must match the baseline (e.g. messages loaded). Last value wins.
    static func fixture(_ name: String, _ value: Int) { push(["k": "f", "n": name, "v": value, "t": now]) }

    /// Level 1 (XCTest): run `body` and return the counts it produced, kept in memory, not written.
    @MainActor static func capture(_ body: () throws -> Void) rethrows -> [String: Int] {
        lock.lock(); capturing = true; counts = [:]; lock.unlock()
        defer { lock.lock(); capturing = false; counts = [:]; lock.unlock() }
        try body()
        lock.lock(); let c = counts; lock.unlock()
        return c
    }

    @MainActor static func start() {
        #if DEBUG
        let debug = true
        #else
        let debug = false
        #endif
        push(["k": "meta", "debug": debug, "t": now])
        push(["k": "m", "n": "process:start", "t": processStart()])
        mark("app:start")
        #if canImport(UIKit)
        if UserDefaults.standard.bool(forKey: "perf") { // set by perf.mjs bench
            HitchMonitor.shared.start()
            BusyMonitor.start()
        }
        #endif
    }

    // MARK: spans: journey -> screen -> phase

    /// Span id, passed to `end`. Nesting comes from `parent`, else the enclosing `span { }` (task-local).
    typealias SpanID = Int
    @TaskLocal static var current: SpanID?
    nonisolated(unsafe) private static var nextID = 0
    nonisolated(unsafe) private static var open: [SpanID: OSSignpostIntervalState] = [:]
    /// The same spans as signpost intervals, so an Instruments recording lines them up with CPU samples.
    private static let signposter = OSSignposter(subsystem: "perf", category: .pointsOfInterest)

    /// Open a span. `phase` is the attribution bucket (navigation, data, render, layout, ...); a span
    /// without one inherits its parent's. Use for work that starts and ends in different callbacks.
    @discardableResult
    static func begin(_ name: String, phase: String? = nil, screen: String? = nil, parent: SpanID? = nil) -> SpanID {
        begin(name, phase: phase, screen: screen, parent: parent, thread: thread)
    }

    private static func begin(_ name: String, phase: String?, screen: String?, parent: SpanID?, thread th: String) -> SpanID {
        let st = signposter.beginInterval("span", id: signposter.makeSignpostID(), "\(name, privacy: .public)")
        lock.lock(); nextID += 1; let id = nextID; open[id] = st; lock.unlock()
        var e: [String: Any] = ["k": "b", "id": id, "n": name, "th": th, "t": now]
        if let p = parent ?? current { e["p"] = p }
        if let phase { e["ph"] = phase }
        if let screen { e["sc"] = screen }
        push(e)
        return id
    }

    static func end(_ id: SpanID) {
        let t = now
        lock.lock(); let st = open.removeValue(forKey: id); lock.unlock()
        if let st { signposter.endInterval("span", st) }
        push(["k": "e", "id": id, "t": t])
    }

    /// Scoped span: everything `body` opens (sync or async) nests under it.
    static func span<T>(_ name: String, phase: String? = nil, screen: String? = nil, _ body: () throws -> T) rethrows -> T {
        let id = begin(name, phase: phase, screen: screen)
        defer { end(id) }
        return try $current.withValue(id) { try body() }
    }

    /// Async scoped span. Its thread is "async": awaiting does not occupy the main thread, so while it
    /// is suspended attribution reads "waiting on <phase>", not main-thread work.
    /// Runs in the caller's isolation (#isolation), so a MainActor caller can pass non-Sendable closures.
    static func span<T>(_ name: String, phase: String? = nil, screen: String? = nil, isolation: isolated (any Actor)? = #isolation,
                        _ body: () async throws -> T) async rethrows -> T {
        let id = begin(name, phase: phase, screen: screen, parent: current, thread: "async")
        defer { end(id) }
        return try await $current.withValue(id) { try await body() }
    }

    /// Screen milestone: "tap" (the user's action), "interactive" (primary control enabled with real
    /// data), "data_ready" (first real data on screen). The cold-launch screen is "launch"; its tap is
    /// process:start. First frames use `firstFrame`, not this.
    static func milestone(_ screen: String, _ m: String) {
        signposter.emitEvent("milestone", "\(screen, privacy: .public):\(m, privacy: .public)")
        mark("\(screen):\(m)")
    }

    #if canImport(UIKit)
    /// Call from onAppear / viewDidAppear. Records the deadline of the next display frame, i.e. the
    /// frame that carries this commit, not the lifecycle callback. `as: "interactive"` when the view
    /// that appeared is the primary control with real data.
    /// ponytail: +-1 frame; exact presentation needs Instruments' display track.
    @MainActor static func firstFrame(_ screen: String, as m: String = "first_frame") {
        FrameProbe.once { push(["k": "m", "n": "\(screen):\(m)", "t": $0]) }
    }
    #endif

    private static var thread: String {
        Thread.isMainThread ? "main" : "bg:\(qos_class_self().rawValue)"
    }

    /// A main-runloop busy stretch (from BusyMonitor). Main-thread time no span covers becomes the
    /// "main (uninstrumented)" bucket instead of "waiting".
    fileprivate static func busy(_ start: Double, _ ms: Double) {
        push(["k": "busy", "th": "main", "t": start, "d": ms])
    }

    fileprivate static func epochMs(media: CFTimeInterval) -> Double { epoch0 + media * 1000 }

    private static func push(_ e: [String: Any]) {
        lock.lock(); buffer.append(e); lock.unlock()
        schedule()
    }

    private static func schedule() {
        lock.lock()
        defer { lock.unlock() }
        if scheduled { return }
        scheduled = true
        queue.asyncAfter(deadline: .now() + 0.2) { flush() }
    }

    private static func flush() {
        lock.lock()
        var events = buffer
        let t = now
        if !capturing { // a capture owns the counts until it returns
            for (n, v) in counts { events.append(["k": "c", "n": n, "v": v, "t": t]) }
            counts = [:]
        }
        buffer = []; scheduled = false
        lock.unlock()
        var data = Data()
        for e in events {
            guard let line = try? JSONSerialization.data(withJSONObject: e) else { continue }
            data.append(line); data.append(0x0A)
        }
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile(); h.write(data); try? h.close()
        } else {
            try? data.write(to: url)
        }
    }

    /// Kernel process start time, so "startup" includes dyld and pre-main work.
    private static func processStart() -> Double {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0 else { return now }
        let tv = info.kp_proc.p_un.__p_starttime
        return Double(tv.tv_sec) * 1000 + Double(tv.tv_usec) / 1000
    }
}

#if canImport(UIKit)
/// Frames that missed their deadline: "hitch" (count) and "hitch:ms" (time past deadline).
/// hitch:ms over the scroll duration is Apple's hitch time ratio.
@MainActor private final class HitchMonitor: NSObject {
    static let shared = HitchMonitor()
    private var link: CADisplayLink?
    private var last: CFTimeInterval = 0

    func start() {
        guard link == nil else { return }
        link = CADisplayLink(target: self, selector: #selector(tick))
        link?.add(to: .main, forMode: .common)
    }

    @objc private func tick(_ l: CADisplayLink) {
        defer { last = l.timestamp }
        guard last > 0 else { return }
        let frame = l.targetTimestamp - l.timestamp
        let late = (l.timestamp - last) - frame
        if late > frame * 0.5 {
            Perf.count("hitch")
            Perf.count("hitch:ms", Int((late * 1000).rounded()))
        }
    }
}
#endif

#if canImport(UIKit)
/// One-shot: the target timestamp (host-epoch ms) of the next display frame.
@MainActor private final class FrameProbe: NSObject {
    private static var live: Set<FrameProbe> = []
    private var link: CADisplayLink?
    private let done: (Double) -> Void

    private init(_ done: @escaping (Double) -> Void) { self.done = done }

    static func once(_ done: @escaping (Double) -> Void) {
        let p = FrameProbe(done)
        live.insert(p)
        p.link = CADisplayLink(target: p, selector: #selector(tick))
        p.link?.add(to: .main, forMode: .common)
    }

    @objc private func tick(_ l: CADisplayLink) {
        l.invalidate()
        done(Perf.epochMs(media: l.targetTimestamp))
        Self.live.remove(self)
    }
}

/// Main-runloop busy stretches: afterWaiting -> beforeWaiting. Stretches under 2ms are dropped.
/// A stretch is only known when it ends, which can be after the harness has read the file, so every
/// main-thread mark cuts the open stretch: busy time up to a milestone is flushed with the milestone.
/// ponytail: sub-2ms work reads as idle; lower `minMs` if attribution shows a large "waiting" bucket.
@MainActor private enum BusyMonitor {
    static let minMs = 2.0
    private static var since = 0.0
    private static var on = false

    static func start() {
        on = true
        // Wake runs first and sleep runs last: Core Animation's commit (SwiftUI layout and render) is a
        // beforeWaiting observer, so a sleep observer ordered before it would call that work idle.
        let wake = CFRunLoopObserverCreateWithHandler(nil, CFRunLoopActivity.afterWaiting.rawValue, true, CFIndex.min) { _, _ in
            MainActor.assumeIsolated { since = Perf.now }
        }
        let sleep = CFRunLoopObserverCreateWithHandler(nil, CFRunLoopActivity.beforeWaiting.rawValue, true, CFIndex.max) { _, _ in
            MainActor.assumeIsolated { cut(); since = 0 }
        }
        CFRunLoopAddObserver(CFRunLoopGetMain(), wake, .commonModes)
        CFRunLoopAddObserver(CFRunLoopGetMain(), sleep, .commonModes)
    }

    /// Emit the open stretch up to now and keep it open from now.
    static func cut() {
        guard on, since > 0 else { return }
        let t = Perf.now
        if t - since >= minMs { Perf.busy(since, t - since) }
        since = t
    }
}
#endif
