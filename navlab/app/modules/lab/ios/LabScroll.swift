// Scripted scroll for the lists lab, shared by both arms (the UIKit twin compiles this file too).
// A CADisplayLink moves the list's UIScrollView at a fixed velocity in time (pt/s, not per frame), so a
// dropped frame makes the next step bigger, as in a real fling. RN gets ordinary scrollViewDidScroll ->
// onScroll events, so virtualization reacts as it would to a finger.
// Each frame it also measures blank area: the part of the viewport not covered by a mounted, visible
// row (a view whose accessibilityIdentifier starts with "row.").

import QuartzCore
import UIKit

final class LabScroller: NSObject {
  private static var active: LabScroller?  // keeps the running driver alive

  private let sv: UIScrollView
  private let speed: CGFloat, distance: CGFloat
  private let handle: Int
  private let done: (String) -> Void
  private var link: CADisplayLink?
  private var last: CFTimeInterval = 0, t0: CFTimeInterval = 0
  private var travelled: CGFloat = 0, stuck = 0
  private var frames = 0, dropped = 0, blankFrames = 0
  private var blankSum: CGFloat = 0, blankMax: CGFloat = 0

  /// Finds the list (view with accessibilityIdentifier "lab.list", or the first UIScrollView inside it),
  /// scrolls it `distance` pt down at `speed` pt/s, ends signpost interval `handle` and calls `done`
  /// with "frames= dropped= blank_frames= blank_pct= blank_max= dist= dur_ms=".
  static func run(speed: Double, distance: Double, handle: Int, done: @escaping (String) -> Void) {
    DispatchQueue.main.async {
      guard active == nil else { return done("error=busy") }  // one scripted scroll at a time
      guard let sv = findList() else {
        labEnd(handle, "error=nolist")
        return done("error=nolist")
      }
      let s = LabScroller(sv: sv, speed: speed, distance: distance, handle: handle, done: done)
      active = s
      s.link = CADisplayLink(target: s, selector: #selector(tick))
      s.link?.add(to: .main, forMode: .common)
    }
  }

  private init(sv: UIScrollView, speed: Double, distance: Double, handle: Int, done: @escaping (String) -> Void) {
    self.sv = sv
    self.speed = speed
    self.distance = distance
    self.handle = handle
    self.done = done
  }

  private static func findList() -> UIScrollView? {
    let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
    func find(_ v: UIView, inList: Bool) -> UIScrollView? {
      let here = inList || v.accessibilityIdentifier == "lab.list"
      if here, let s = v as? UIScrollView { return s }
      for c in v.subviews { if let s = find(c, inList: here) { return s } }
      return nil
    }
    return windows.lazy.compactMap { find($0, inList: false) }.first
  }

  @objc private func tick(_ l: CADisplayLink) {
    let frame = l.targetTimestamp - l.timestamp
    if last == 0 {
      last = l.timestamp
      t0 = l.timestamp
      return
    }
    let dt = l.timestamp - last
    last = l.timestamp
    frames += 1
    let missed = Int((dt / frame).rounded()) - 1
    if missed > 0 { dropped += missed }

    let maxY = max(0, sv.contentSize.height - sv.bounds.height + sv.adjustedContentInset.bottom)
    let step = min(speed * CGFloat(dt), distance - travelled)
    let y = min(sv.contentOffset.y + step, maxY)
    travelled += y - sv.contentOffset.y
    stuck = y - sv.contentOffset.y < 0.5 ? stuck + 1 : 0
    sv.contentOffset.y = y
    // UIKit adds newly exposed cells in layoutSubviews, which would otherwise run later in this same
    // frame's commit: lay out now so the blank measurement sees what the frame will show.
    sv.layoutIfNeeded()
    measureBlank()

    // Done: distance covered, or the content ended (virtualized lists can grow their content size, so
    // allow a few frames for it to catch up before calling it the end).
    if travelled >= distance - 0.5 || stuck > 30 { finish(l.timestamp) }
  }

  private func measureBlank() {
    let vis = sv.bounds
    guard vis.height > 0 else { return }
    var spans: [(CGFloat, CGFloat)] = []
    func walk(_ v: UIView) {
      for c in v.subviews where !c.isHidden && c.alpha > 0.01 {
        let r = c.convert(c.bounds, to: sv)
        guard r.intersects(vis) else { continue }
        if c.accessibilityIdentifier?.hasPrefix("row.") == true {
          spans.append((max(r.minY, vis.minY), min(r.maxY, vis.maxY)))
        } else {
          walk(c)
        }
      }
    }
    walk(sv)
    spans.sort { $0.0 < $1.0 }
    var covered: CGFloat = 0, end = vis.minY
    for (a, b) in spans where b > end {
      covered += b - max(a, end)
      end = b
    }
    let blank = max(0, 1 - covered / vis.height)
    blankSum += blank
    blankMax = max(blankMax, blank)
    if blank > 0.01 { blankFrames += 1 }
  }

  private func finish(_ now: CFTimeInterval) {
    link?.invalidate()
    link = nil
    let msg = String(format: "frames=%d dropped=%d blank_frames=%d blank_pct=%.2f blank_max=%.2f dist=%.0f dur_ms=%.0f",
                     frames, dropped, blankFrames, frames > 0 ? 100 * blankSum / CGFloat(frames) : 0,
                     100 * blankMax, travelled, (now - t0) * 1000)
    labEnd(handle, msg)
    done(msg)
    LabScroller.active = nil
  }
}
