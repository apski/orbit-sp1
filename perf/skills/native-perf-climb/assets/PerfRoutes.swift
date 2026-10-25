// Level 2 for perf-climb: test-only routes that mount one subsystem with fixture data in the real app,
// skipping startup, navigation and network. `perf.mjs bench` writes "chat?n=3000" (or "blank") to
// Library/Caches/perf-route.txt in the app container, and watch() mounts it. A file is used rather
// than a deep link because iOS asks "Open in …?" on every external custom-scheme open.
// Compiled only when the perf build passes SWIFT_ACTIVE_COMPILATION_CONDITIONS='$(inherited) PERF_ROUTES'.
// `perf.mjs leakcheck <App.app>` fails if this type reaches a normal release build.
// Wiring, on the root view:
//     #if PERF_ROUTES
//     @State private var perfRoute: (path: String, id: UUID)?
//     ...
//     .overlay { if let r = perfRoute { PerfRoutes.view(for: r.path).id(r.id) } }
//     .task { await PerfRoutes.watch { path in Perf.mark("route:open"); perfRoute = (path, UUID()) } }
//     #endif

#if PERF_ROUTES
import SwiftUI

enum PerfRoutes {
    static let marker = "PERF_ROUTES_ENABLED"

    /// "chat?n=3000" -> the subsystem view. "blank" (or anything unknown) clears the screen.
    @MainActor @ViewBuilder static func view(for route: String) -> some View {
        let parts = route.split(separator: "?", maxSplits: 1).map(String.init)
        let q = URLComponents(string: "x://x?" + (parts.count > 1 ? parts[1] : ""))?.queryItems ?? []
        let n = q.first { $0.name == "n" }?.value.flatMap(Int.init) ?? 3000
        switch parts.first ?? "" {
        case "chat": ChatList(msgs: PerfFixture.messages(n)).background(Color(.systemBackground)) // <- your subsystems
        default: Color(.systemBackground).accessibilityIdentifier(marker)
        }
    }

    /// Polls the route file every 50ms. That latency falls before the "route:open" mark, so it stays
    /// outside the measured span.
    @MainActor static func watch(_ open: @MainActor (String) -> Void) async {
        let url = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("perf-route.txt")
        var seen = ""
        while !Task.isCancelled {
            if let text = try? String(contentsOf: url, encoding: .utf8), text != seen {
                seen = text
                open(String(text.split(separator: "\n").first ?? ""))
            }
            try? await Task.sleep(for: .milliseconds(50))
        }
    }
}
#endif
