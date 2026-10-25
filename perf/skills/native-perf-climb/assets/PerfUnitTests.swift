// Level 1 for perf-climb: mount one subsystem in a hosted XCTest, with no flows or UI driving.
// Copy into a unit-test target hosted by the app (TEST_HOST, ENABLE_TESTABILITY=YES). perf.mjs runs it
// once per bench with PERF_REPS / PERF_WARMUP (sent as TEST_RUNNER_PERF_*) and parses the METRIC lines.
// Trust the counts (body evaluations, cell configs, requests). The timings are indicative only: this
// is a test host, not the app's real launch and navigation path.

import QuartzCore
import SwiftUI
import XCTest
@testable import App // <- your app module

@MainActor final class PerfUnitTests: XCTestCase {
    func testChatListMount() {
        // Mount the subsystem directly with pinned fixture data: data in, view out.
        bench("chat_mount") { ChatList(msgs: PerfFixture.messages(3000)) }
    }

    /// Mounts `content` in a real window, then reports `<name>_ms` (first render and layout) plus every
    /// Perf.count produced while it settles (onAppear, .task, lazy stack prefetch).
    private func bench<V: View>(_ name: String, settle: TimeInterval = 0.1, _ content: () -> V) {
        let env = ProcessInfo.processInfo.environment
        let reps = Int(env["PERF_REPS"] ?? "") ?? 7, warmup = Int(env["PERF_WARMUP"] ?? "") ?? 1
        #if DEBUG
        print("META debug=true")
        #else
        print("META debug=false")
        #endif
        for i in 0..<(warmup + reps) {
            var ms = 0.0
            let counts = Perf.capture {
                let t0 = CACurrentMediaTime()
                let host = UIHostingController(rootView: content())
                let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 402, height: 874))
                window.rootViewController = host
                window.makeKeyAndVisible()
                host.view.layoutIfNeeded()
                ms = (CACurrentMediaTime() - t0) * 1000
                RunLoop.main.run(until: Date().addingTimeInterval(settle))
                window.isHidden = true
            }
            guard i >= warmup else { continue }
            print("METRIC \(name)_ms=\(ms)")
            for (k, v) in counts.sorted(by: { $0.key < $1.key }) { print("METRIC count:\(k)=\(v)") }
        }
    }
}
