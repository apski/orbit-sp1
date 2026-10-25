import Foundation
import XCTest
@testable import FlexLayout

/// Replays oracle/gen.mjs cases (yoga-layout, RN config) through FlexCore and diffs every frame.
/// Regenerate: `node oracle/gen.mjs --n 2000`. Tolerance 0.01pt (Yoga is float32; no pixel rounding).
final class YogaParityTests: XCTestCase {
  struct Spec: Decodable { let style: FlexStyle; let measure: Measure?; let children: [Spec]? }
  struct Measure: Decodable { let kind: String; let n: Int?; let w: Float?; let h: Float? }
  struct Case: Decodable { let id: Int; let width: Float; let height: Float?; let root: Spec; let frames: [[Float?]] }  // null = NaN (Yoga can yield it)
  struct File: Decodable { let oracle: String; let cases: [Case] }

  /// Mirror of measureText in oracle/gen.mjs.
  static func measureText(_ n: Int, _ width: Float, _ mode: MeasureMode) -> (width: Float, height: Float) {
    if mode == .undefined || width.isNaN { return (Float(n * 7), 17) }
    let cpl = min(n, max(1, Int((Double(width) / 7).rounded(.down))))
    let lines = (n + cpl - 1) / cpl
    return (mode == .exactly ? width : Float(cpl * 7), Float(lines * 17))
  }

  static func build(_ s: Spec) -> FlexNode {
    let node = FlexNode(s.style)
    if let m = s.measure {
      if m.kind == "text", let n = m.n { node.measure = { w, wm, _, _ in measureText(n, w, wm) } }
      if m.kind == "fixed", let w = m.w, let h = m.h { node.measure = { _, _, _, _ in (w, h) } }
    }
    s.children?.forEach { node.append(build($0)) }
    return node
  }

  static func frames(_ n: FlexNode, _ out: inout [[Float]]) {
    out.append([n.left, n.top, n.width, n.height])
    n.children.forEach { frames($0, &out) }
  }

  func testYogaParity() throws {
    let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../cases/cases.json")
    guard let data = try? Data(contentsOf: url) else {
      throw XCTSkip("no oracle cases; run `node oracle/gen.mjs` first")
    }
    let file = try JSONDecoder().decode(File.self, from: data)
    var failed: [(Int, String)] = []
    for c in file.cases {
      let root = Self.build(c.root)
      root.calculateLayout(width: c.width, height: c.height ?? .nan)
      var got: [[Float]] = []
      Self.frames(root, &got)
      if ProcessInfo.processInfo.environment["FLEX_CASE"] == "\(c.id)" { print("CASE \(c.id) got \(got)") }
      if got.count != c.frames.count { failed.append((c.id, "node count \(got.count) vs \(c.frames.count)")); continue }
      if let i = got.indices.first(where: { zip(got[$0], c.frames[$0]).contains { g, w in w.map { abs(g - $0) > 0.01 } ?? !g.isNaN } }) {
        failed.append((c.id, "node \(i): got \(got[i]) want \(c.frames[i])"))
      }
    }
    let pass = file.cases.count - failed.count
    print("PARITY \(pass)/\(file.cases.count) (\(file.oracle))")
    for (id, msg) in failed.prefix(8) { print("  case \(id): \(msg)") }
    try? failed.map { "\($0.0)" }.joined(separator: "\n")
      .write(to: url.deletingLastPathComponent().appendingPathComponent("failed.txt"), atomically: true, encoding: .utf8)
    // Known divergences (expected to fail) for the committed dev set: `node oracle/gen.mjs --n 2000 --seed 1` against RN's
    // vendored Yoga. Absolute positioning with conflicting constraints, and clang FMA rounding at float knife-edges.
    // A new failure fails the test; a fixed one is reported so it gets removed from the list.
    let known = Set((try? String(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .appendingPathComponent("known-divergences.txt"), encoding: .utf8))?.split(whereSeparator: \.isNewline).compactMap { Int($0) } ?? [])
    let ids = Set(failed.map(\.0))
    if file.cases.count == 2000 && !known.isEmpty {
      let fixed = known.subtracting(ids)
      if !fixed.isEmpty { print("FIXED (remove from known-divergences.txt): \(fixed.sorted())") }
      XCTAssertEqual(ids.subtracting(known).sorted(), [], "new divergences from Yoga")
    } else {
      XCTAssertEqual(failed.count, 0)
    }
  }
}
