// Flex: one SwiftUI Layout per composition. The container tree is data (FlexSpec) laid out by FlexCore, so Yoga's
// sizing modes survive, which nested SwiftUI layouts would lose. Only leaves (text, images, mapped components) and
// decorations (backgrounds) are subviews, in spec preorder, so z-order matches RN's paint order.
import SwiftUI

public struct FlexSpec: Codable, Sendable {
  public var style: FlexStyle
  /// Subview index of a measured leaf (text, image, component).
  public var leaf: Int?
  /// Subview index of a decoration placed at this node's frame and never measured.
  public var decoration: Int?
  /// Leaf anchor inside its frame when the frame is wider than the content (RN textAlign).
  public var align: String?
  /// Edges ("t", "b", "l", "r") whose runtime safe-area inset is added to the padding: RN SafeAreaView ("tblr"),
  /// or a ScrollView with contentInsetAdjustmentBehavior "automatic" on the edges it touches.
  public var safeArea: String?
  public var children: [FlexSpec]?

  public static func decode(_ json: String) -> FlexSpec {
    try! JSONDecoder().decode(FlexSpec.self, from: Data(json.utf8))
  }
}

public struct Flex: Layout {
  let spec: FlexSpec
  let safeArea: EdgeInsets
  /// Called with every node's frame (preorder, in the Layout's coordinates) after placement; for parity judging.
  let onFrames: (([CGRect]) -> Void)?

  public init(_ spec: FlexSpec, safeArea: EdgeInsets = EdgeInsets(), onFrames: (([CGRect]) -> Void)? = nil) {
    self.spec = spec; self.safeArea = safeArea; self.onFrames = onFrames
  }

  final class Built { let node: FlexNode; let spec: FlexSpec; var kids: [Built] = []
    init(_ n: FlexNode, _ s: FlexSpec) { node = n; spec = s } }

  func build(_ s: FlexSpec, _ subviews: Subviews) -> Built {
    var style = s.style
    if let edges = s.safeArea {
      if edges.contains("t") { style.paddingTop = (style.paddingTop ?? 0) + Float(safeArea.top) }
      if edges.contains("b") { style.paddingBottom = (style.paddingBottom ?? 0) + Float(safeArea.bottom) }
      if edges.contains("l") { style.paddingLeft = (style.paddingLeft ?? 0) + Float(safeArea.leading) }
      if edges.contains("r") { style.paddingRight = (style.paddingRight ?? 0) + Float(safeArea.trailing) }
    }
    let b = Built(FlexNode(style), s)
    if let i = s.leaf {
      let view = subviews[i]
      b.node.measure = { w, wm, h, hm in
        let size = view.sizeThatFits(ProposedViewSize(width: wm == .undefined ? nil : CGFloat(w),
                                                      height: hm == .undefined ? nil : CGFloat(h)))
        return (Float(size.width), Float(size.height))
      }
    }
    for c in s.children ?? [] { let k = build(c, subviews); b.kids.append(k); b.node.append(k.node) }
    return b
  }

  func layout(_ proposal: ProposedViewSize, _ subviews: Subviews) -> Built {
    let root = build(spec, subviews)
    root.node.calculateLayout(width: proposal.width.map(Float.init) ?? .nan, height: proposal.height.map(Float.init) ?? .nan)
    return root
  }

  public func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let root = layout(proposal, subviews).node
    return CGSize(width: CGFloat(root.width), height: CGFloat(root.height))
  }

  public func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    let root = layout(ProposedViewSize(bounds.size), subviews)
    var frames: [CGRect] = []
    func place(_ b: Built, _ x: CGFloat, _ y: CGFloat) {
      let f = CGRect(x: x + CGFloat(b.node.left), y: y + CGFloat(b.node.top),
                     width: CGFloat(b.node.width), height: CGFloat(b.node.height))
      frames.append(f)
      if let d = b.spec.decoration {
        subviews[d].place(at: f.origin, anchor: .topLeading, proposal: ProposedViewSize(f.size))
      }
      if let l = b.spec.leaf {  // content box: Yoga measured the leaf without its padding
        let s = b.node.style
        let c = CGRect(x: f.minX + CGFloat(s.paddingLeft ?? 0), y: f.minY + CGFloat(s.paddingTop ?? 0),
                       width: f.width - CGFloat(b.node.paddingAndBorder(row: true)),
                       height: f.height - CGFloat(b.node.paddingAndBorder(row: false)))
        let (point, anchor): (CGPoint, UnitPoint) = switch b.spec.align {
          case "center": (CGPoint(x: c.midX, y: c.minY), .top)
          case "right": (CGPoint(x: c.maxX, y: c.minY), .topTrailing)
          default: (c.origin, .topLeading)
        }
        subviews[l].place(at: point, anchor: anchor, proposal: ProposedViewSize(c.size))
      }
      for k in b.kids { place(k, f.minX, f.minY) }
    }
    place(root, bounds.minX, bounds.minY)
    onFrames?(frames)
  }
}
