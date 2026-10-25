import SwiftUI
import UIKit

/// Frame probes for the native-stack back-ends (backends/swiftui/lower.mjs, backends/uikit/lower.mjs). Each IR node
/// reports its window frame; the screen logs them all as one composition (0) in preorder for the parity judge.

// MARK: SwiftUI: `.spectraFrame(i)` per node, `.spectraFrames(count, onFrames)` once at the root.

struct SpectraFrames: PreferenceKey {
  static let defaultValue: [Int: CGRect] = [:]
  static func reduce(value: inout [Int: CGRect], nextValue: () -> [Int: CGRect]) { value.merge(nextValue()) { $1 } }
}

extension View {
  func spectraFrame(_ i: Int) -> some View {
    background(GeometryReader { Color.clear.preference(key: SpectraFrames.self, value: [i: $0.frame(in: .global)]) })
  }

  func spectraFrames(_ count: Int, _ onFrames: ((Int, [CGRect]) -> Void)?) -> some View {
    nonisolated(unsafe) let report = onFrames  // only called on the main thread by SwiftUI
    return onPreferenceChange(SpectraFrames.self) { f in
      report?(0, (0..<count).map { f[$0] ?? CGRect(x: -1, y: -1, width: 0, height: 0) })
    }
  }
}

// MARK: UIKit: the generated view lists its probes; the host logs them after every layout pass.

@MainActor protocol SpectraProbed: UIView {
  var probes: [(UIView, UIEdgeInsets)] { get }
}

struct SpectraUIKitHost<V: SpectraProbed>: UIViewControllerRepresentable {
  let make: () -> V
  let onFrames: ((Int, [CGRect]) -> Void)?

  func makeUIViewController(context: Context) -> SpectraProbeController { SpectraProbeController(make(), onFrames: onFrames) }
  func updateUIViewController(_ vc: SpectraProbeController, context: Context) {}
}

/// Hosts the generated UIKit view the way a UIKit app would: it sees the window's real safe-area insets, which SwiftUI
/// zeroes for representables under .ignoresSafeArea() (UIScrollView's automatic inset adjustment depends on them).
final class SpectraProbeController: UIViewController {
  private let screen: SpectraProbed
  private let onFrames: ((Int, [CGRect]) -> Void)?

  init(_ screen: SpectraProbed, onFrames: ((Int, [CGRect]) -> Void)?) {
    self.screen = screen
    self.onFrames = onFrames
    super.init(nibName: nil, bundle: nil)
  }

  required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

  override func loadView() { view = screen }

  override func viewDidLayoutSubviews() {
    super.viewDidLayoutSubviews()
    guard let window = view.window else { return }
    // What the window's safe area overlaps of this view, minus what SwiftUI already passed down.
    let w = window.safeAreaInsets, f = view.convert(view.bounds, to: nil), b = window.bounds
    let want = UIEdgeInsets(top: max(0, w.top - f.minY), left: max(0, w.left - f.minX),
                            bottom: max(0, f.maxY - (b.height - w.bottom)), right: max(0, f.maxX - (b.width - w.right)))
    let a = additionalSafeAreaInsets, inherited = view.safeAreaInsets
    let extra = UIEdgeInsets(top: max(0, want.top - (inherited.top - a.top)), left: max(0, want.left - (inherited.left - a.left)),
                             bottom: max(0, want.bottom - (inherited.bottom - a.bottom)), right: max(0, want.right - (inherited.right - a.right)))
    if extra != a { additionalSafeAreaInsets = extra }
  }

  override func viewDidAppear(_ animated: Bool) {
    super.viewDidAppear(animated)
    // Report once layout (stack views, scroll inset adjustment) has settled; the judge reads the last line.
    for delay in [0.3, 1.0, 2.0] { DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.report() } }
  }

  private func report() {
    view.layoutIfNeeded()
    onFrames?(0, screen.probes.map { v, o in
      let r = v.convert(v.bounds, to: nil)
      return CGRect(x: r.minX - o.left, y: r.minY - o.top, width: r.width + o.left + o.right, height: r.height + o.top + o.bottom)
    })
    // Auto Layout's own verdict: an ambiguous view has more than one valid frame, so parity on it would be luck.
    func all(_ v: UIView) -> [UIView] { [v] + v.subviews.flatMap(all) }
    NSLog("ORBIT_AMBIGUOUS %d", all(screen).filter(\.hasAmbiguousLayout).count)
  }
}
