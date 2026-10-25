// FlexCore: a Float32 port of the subset of Yoga's flex algorithm that React Native layouts use, so generated SwiftUI
// lays out exactly like the RN screen it came from. Function names follow yoga/algorithm/CalculateLayout.cpp
// (RN 0.76.5) so every branch can be checked against the source. Errata: RN's default (StretchFlexBasis on).
// Undefined is Float.nan, as YGUndefined is in Yoga.
// Absolute children follow RN's errata (AbsolutePositioningIncorrect, AbsolutePercentAgainstInnerSize) and every
// node is a containing block (RN's default position is relative).
// ponytail: no wrap, aspectRatio, baseline, percentages, auto margins, borders, display:none or RTL yet.
// Each is a reported gap, added when the oracle cases cover it.

public enum FlexDirection: String, Codable, Sendable { case row, column }
public enum Justify: String, Codable, Sendable {
  case flexStart = "flex-start", center, flexEnd = "flex-end"
  case spaceBetween = "space-between", spaceAround = "space-around", spaceEvenly = "space-evenly"
}
public enum Align: String, Codable, Sendable { case auto, stretch, flexStart = "flex-start", center, flexEnd = "flex-end" }
public enum MeasureMode: Sendable { case undefined, exactly, atMost }
public enum PositionType: String, Codable, Sendable { case relative, absolute }

enum SizingMode {
  case stretchFit, fitContent, maxContent
  var measureMode: MeasureMode {
    switch self { case .stretchFit: .exactly; case .fitContent: .atMost; case .maxContent: .undefined }
  }
}

/// RN style keys, so a captured RN style decodes directly.
public struct FlexStyle: Codable, Sendable {
  public var flexDirection: FlexDirection?
  public var justifyContent: Justify?
  public var alignItems: Align?
  public var alignSelf: Align?
  public var flexGrow, flexShrink, flex, flexBasis: Float?
  public var width, height, minWidth, maxWidth, minHeight, maxHeight: Float?
  public var paddingTop, paddingRight, paddingBottom, paddingLeft: Float?
  public var marginTop, marginRight, marginBottom, marginLeft: Float?
  public var rowGap, columnGap: Float?
  public var position: PositionType?
  public var top, right, bottom, left: Float?
  public init() {}
}

public final class FlexNode {
  public var style: FlexStyle
  public private(set) var children: [FlexNode] = []
  public var measure: ((_ width: Float, _ widthMode: MeasureMode, _ height: Float, _ heightMode: MeasureMode) -> (width: Float, height: Float))?
  weak var owner: FlexNode?

  /// Result, relative to the parent's top-left.
  public internal(set) var left: Float = 0, top: Float = 0, width: Float = 0, height: Float = 0
  var measuredWidth: Float = .nan, measuredHeight: Float = .nan
  var computedFlexBasis: Float = .nan
  var resolvedWidth: Float = .nan, resolvedHeight: Float = .nan
  // Measurement cache (node/LayoutResults.h). Yoga reuses results under lenient rules (algorithm/Cache.cpp), and RN
  // layouts depend on that reuse, so it is part of the semantics, not just an optimisation.
  var isDirty = true
  var generationCount: UInt32 = 0
  var cachedLayout = CachedMeasurement()
  var cachedMeasurements = [CachedMeasurement](repeating: CachedMeasurement(), count: 8)
  var nextCachedMeasurementsIndex = 0

  public init(_ style: FlexStyle = FlexStyle()) { self.style = style }
  public func append(_ child: FlexNode) { child.owner = self; children.append(child) }
  nonisolated(unsafe) static var currentGenerationCount: UInt32 = 0
  func markDirtyRecursively() { isDirty = true; children.forEach { $0.markDirtyRecursively() } }

  /// Yoga calculateLayout(node, ownerWidth, ownerHeight, LTR). Pass .nan for an unconstrained axis.
  public func calculateLayout(width ownerWidth: Float, height ownerHeight: Float) {
    resolveDimension()
    var width = Float.nan, height = Float.nan
    var wMode = SizingMode.maxContent, hMode = SizingMode.maxContent
    if hasDefiniteLength(row: true) { width = resolvedWidth + margin(row: true); wMode = .stretchFit }
    else if defined(maxDim(row: true)) { width = maxDim(row: true); wMode = .fitContent }
    else { width = ownerWidth; wMode = defined(width) ? .stretchFit : .maxContent }
    if hasDefiniteLength(row: false) { height = resolvedHeight + margin(row: false); hMode = .stretchFit }
    else if defined(maxDim(row: false)) { height = maxDim(row: false); hMode = .fitContent }
    else { height = ownerHeight; hMode = defined(height) ? .stretchFit : .maxContent }
    FlexNode.currentGenerationCount &+= 1
    markDirtyRecursively()  // ponytail: whole tree re-lays out on every call; add per-node dirtying if it shows in profiles
    calculateLayoutInternal(self, width, height, wMode, hMode, ownerWidth, ownerHeight, performLayout: true)
    setInitialPosition()
  }
}

// MARK: - Float helpers (yoga/numeric/Comparison.h)

@inline(__always) func defined(_ v: Float) -> Bool { !v.isNaN }
@inline(__always) func maxOrDefined(_ a: Float, _ b: Float) -> Float { a.isNaN ? b : b.isNaN ? a : Swift.max(a, b) }
@inline(__always) func minOrDefined(_ a: Float, _ b: Float) -> Float { a.isNaN ? b : b.isNaN ? a : Swift.min(a, b) }
@inline(__always) func opt(_ v: Float?) -> Float { v ?? .nan }

// MARK: - Node accessors (yoga/node/Node.cpp, style/Style.h)

extension FlexNode {
  var direction: FlexDirection { style.flexDirection ?? .column }
  var justify: Justify { style.justifyContent ?? .flexStart }
  var alignItems: Align { style.alignItems ?? .stretch }

  func resolveDimension() {
    for row in [true, false] {
      let mx = opt(row ? style.maxWidth : style.maxHeight), mn = opt(row ? style.minWidth : style.minHeight)
      let dim = opt(row ? style.width : style.height)
      let v = defined(mx) && defined(mn) && abs(mx - mn) < 0.0001 ? mx : dim
      if row { resolvedWidth = v } else { resolvedHeight = v }
    }
  }
  func resolved(row: Bool) -> Float { row ? resolvedWidth : resolvedHeight }
  func hasDefiniteLength(row: Bool) -> Bool { let v = resolved(row: row); return defined(v) && v >= 0 }
  func minDim(row: Bool) -> Float { opt(row ? style.minWidth : style.minHeight) }
  func maxDim(row: Bool) -> Float { opt(row ? style.maxWidth : style.maxHeight) }

  func resolveFlexGrow() -> Float {
    guard owner != nil else { return 0 }
    if let g = style.flexGrow { return g }
    if let f = style.flex, f > 0 { return f }
    return 0
  }
  func resolveFlexShrink() -> Float {
    guard owner != nil else { return 0 }
    if let s = style.flexShrink { return s }
    if let f = style.flex, f < 0 { return -f }
    return 0
  }
  func resolveFlexBasis() -> Float {
    if let b = style.flexBasis { return b }
    if let f = style.flex, f > 0 { return 0 }
    return .nan
  }
  var isNodeFlexible: Bool { resolveFlexGrow() != 0 || resolveFlexShrink() != 0 }

  func paddingAndBorder(row: Bool) -> Float {
    row ? (style.paddingLeft ?? 0) + (style.paddingRight ?? 0) : (style.paddingTop ?? 0) + (style.paddingBottom ?? 0)
  }
  func flexStartPadding(row: Bool) -> Float { row ? style.paddingLeft ?? 0 : style.paddingTop ?? 0 }
  func flexEndPadding(row: Bool) -> Float { row ? style.paddingRight ?? 0 : style.paddingBottom ?? 0 }
  /// computeGapForAxis: the row axis uses columnGap.
  func gap(row: Bool) -> Float { row ? style.columnGap ?? 0 : style.rowGap ?? 0 }
  func measured(row: Bool) -> Float { row ? measuredWidth : measuredHeight }
  func setMeasured(_ v: Float, row: Bool) { if row { measuredWidth = v } else { measuredHeight = v } }
  func setPosition(_ v: Float, row: Bool) { if row { left = v } else { top = v } }
  func position(row: Bool) -> Float { row ? left : top }

  var isAbsolute: Bool { style.position == .absolute }
  func leadingMargin(row: Bool) -> Float { row ? style.marginLeft ?? 0 : style.marginTop ?? 0 }
  func trailingMargin(row: Bool) -> Float { row ? style.marginRight ?? 0 : style.marginBottom ?? 0 }
  func margin(row: Bool) -> Float { leadingMargin(row: row) + trailingMargin(row: row) }
  func dimensionWithMargin(row: Bool) -> Float { measured(row: row) + margin(row: row) }
  func leadingInset(row: Bool) -> Float? { row ? style.left : style.top }
  func trailingInset(row: Bool) -> Float? { row ? style.right : style.bottom }
  /// Node::relativePosition: the leading inset wins, else minus the trailing one.
  func relativePosition(row: Bool) -> Float { leadingInset(row: row) ?? -(trailingInset(row: row) ?? 0) }
  /// Node::setPosition: leading margin plus relative offset.
  func setInitialPosition() {
    left = leadingMargin(row: true) + relativePosition(row: true)
    top = leadingMargin(row: false) + relativePosition(row: false)
  }
}

func resolveChildAlignment(_ node: FlexNode, _ child: FlexNode) -> Align {
  let a = child.style.alignSelf ?? .auto
  return a == .auto ? node.alignItems : a
}

// MARK: - BoundAxis.h

func boundAxisWithinMinAndMax(_ node: FlexNode, row: Bool, _ value: Float) -> Float {
  let mn = node.minDim(row: row), mx = node.maxDim(row: row)
  if defined(mx), mx >= 0, value > mx { return mx }
  if defined(mn), mn >= 0, value < mn { return mn }
  return value
}

func boundAxis(_ node: FlexNode, row: Bool, _ value: Float) -> Float {
  maxOrDefined(boundAxisWithinMinAndMax(node, row: row, value), node.paddingAndBorder(row: row))
}

func constrainMaxSizeForMode(_ node: FlexNode, row: Bool, _ mode: inout SizingMode, _ size: inout Float) {
  let maxSize = node.maxDim(row: row) + node.margin(row: row)
  switch mode {
  case .stretchFit, .fitContent: size = (maxSize.isNaN || size < maxSize) ? size : maxSize
  case .maxContent: if defined(maxSize) { mode = .fitContent; size = maxSize }
  }
}

// MARK: - CalculateLayout.cpp

struct CachedMeasurement {
  var availableWidth: Float = -1, availableHeight: Float = -1
  var widthMode = SizingMode.maxContent, heightMode = SizingMode.maxContent
  var computedWidth: Float = -1, computedHeight: Float = -1
}

@inline(__always) func inexactEquals(_ a: Float, _ b: Float) -> Bool {
  defined(a) && defined(b) ? abs(a - b) < 0.0001 : a.isNaN && b.isNaN
}

/// algorithm/Cache.cpp (pointScaleFactor 0: no rounded comparison).
func canUseCachedMeasurement(_ widthMode: SizingMode, _ availableWidth: Float, _ heightMode: SizingMode,
                             _ availableHeight: Float, _ last: CachedMeasurement,
                             _ marginRow: Float, _ marginColumn: Float) -> Bool {
  if (defined(last.computedHeight) && last.computedHeight < 0) || (defined(last.computedWidth) && last.computedWidth < 0) {
    return false
  }
  func compatible(_ mode: SizingMode, _ available: Float, _ margin: Float, _ lastMode: SizingMode, _ lastSize: Float,
                  _ lastComputed: Float) -> Bool {
    let size = available - margin  // the same-spec check uses the raw size, the rest the size without margins
    return (lastMode == mode && inexactEquals(lastSize, available))
      || (mode == .stretchFit && inexactEquals(size, lastComputed))
      || (mode == .fitContent && lastMode == .maxContent && (size >= lastComputed || inexactEquals(size, lastComputed)))
      || (lastMode == .fitContent && mode == .fitContent && defined(lastSize) && defined(size) && defined(lastComputed)
          && lastSize > size && (lastComputed <= size || inexactEquals(size, lastComputed)))
  }
  return compatible(widthMode, availableWidth, marginRow, last.widthMode, last.availableWidth, last.computedWidth)
    && compatible(heightMode, availableHeight, marginColumn, last.heightMode, last.availableHeight, last.computedHeight)
}

func calculateLayoutInternal(_ node: FlexNode, _ availableWidth: Float, _ availableHeight: Float,
                             _ widthMode: SizingMode, _ heightMode: SizingMode,
                             _ ownerWidth: Float, _ ownerHeight: Float, performLayout: Bool) {
  let generation = FlexNode.currentGenerationCount
  let needToVisitNode = node.isDirty && node.generationCount != generation
  if needToVisitNode {
    node.nextCachedMeasurementsIndex = 0
    node.cachedLayout = CachedMeasurement()
  }
  func exact(_ c: CachedMeasurement) -> Bool {
    inexactEquals(c.availableWidth, availableWidth) && inexactEquals(c.availableHeight, availableHeight)
      && c.widthMode == widthMode && c.heightMode == heightMode
  }
  var cached: CachedMeasurement?
  let measures = node.cachedMeasurements[0..<node.nextCachedMeasurementsIndex]
  if node.measure != nil {
    let mr = node.margin(row: true), mc = node.margin(row: false)
    cached = canUseCachedMeasurement(widthMode, availableWidth, heightMode, availableHeight, node.cachedLayout, mr, mc)
      ? node.cachedLayout
      : measures.first { canUseCachedMeasurement(widthMode, availableWidth, heightMode, availableHeight, $0, mr, mc) }
  } else if performLayout {
    cached = exact(node.cachedLayout) ? node.cachedLayout : nil
  } else {
    cached = measures.first(where: exact)
  }

  if !needToVisitNode, let c = cached {
    node.measuredWidth = c.computedWidth
    node.measuredHeight = c.computedHeight
  } else {
    calculateLayoutImpl(node, availableWidth, availableHeight, widthMode, heightMode, ownerWidth, ownerHeight, performLayout)
    if cached == nil {
      if node.nextCachedMeasurementsIndex == node.cachedMeasurements.count { node.nextCachedMeasurementsIndex = 0 }
      let entry = CachedMeasurement(availableWidth: availableWidth, availableHeight: availableHeight,
                                    widthMode: widthMode, heightMode: heightMode,
                                    computedWidth: node.measuredWidth, computedHeight: node.measuredHeight)
      if performLayout {
        node.cachedLayout = entry
      } else {
        node.cachedMeasurements[node.nextCachedMeasurementsIndex] = entry
        node.nextCachedMeasurementsIndex += 1
      }
    }
  }
  if performLayout {
    node.width = node.measuredWidth; node.height = node.measuredHeight
    node.isDirty = false
  }
  node.generationCount = generation
}

func computeFlexBasisForChild(_ node: FlexNode, _ child: FlexNode, _ width: Float, _ widthMode: SizingMode,
                              _ height: Float, _ ownerWidth: Float, _ ownerHeight: Float, _ heightMode: SizingMode) {
  let isMainAxisRow = node.direction == .row
  let mainAxisSize = isMainAxisRow ? width : height
  let resolvedFlexBasis = child.resolveFlexBasis()
  let isRowStyleDimDefined = child.hasDefiniteLength(row: true)
  let isColumnStyleDimDefined = child.hasDefiniteLength(row: false)

  if defined(resolvedFlexBasis) && defined(mainAxisSize) {
    if child.computedFlexBasis.isNaN {
      child.computedFlexBasis = maxOrDefined(resolvedFlexBasis, child.paddingAndBorder(row: isMainAxisRow))
    }
  } else if isMainAxisRow && isRowStyleDimDefined {
    child.computedFlexBasis = maxOrDefined(child.resolvedWidth, child.paddingAndBorder(row: true))
  } else if !isMainAxisRow && isColumnStyleDimDefined {
    child.computedFlexBasis = maxOrDefined(child.resolvedHeight, child.paddingAndBorder(row: false))
  } else {
    var childWidth = Float.nan, childHeight = Float.nan
    var childWidthMode = SizingMode.maxContent, childHeightMode = SizingMode.maxContent
    if isRowStyleDimDefined { childWidth = child.resolvedWidth + child.margin(row: true); childWidthMode = .stretchFit }
    if isColumnStyleDimDefined { childHeight = child.resolvedHeight + child.margin(row: false); childHeightMode = .stretchFit }
    if childWidth.isNaN && defined(width) { childWidth = width; childWidthMode = .fitContent }
    if childHeight.isNaN && defined(height) { childHeight = height; childHeightMode = .fitContent }

    let stretch = resolveChildAlignment(node, child) == .stretch
    if !isMainAxisRow && !isRowStyleDimDefined && defined(width) && widthMode == .stretchFit
      && stretch && childWidthMode != .stretchFit {
      childWidth = width; childWidthMode = .stretchFit
    }
    if isMainAxisRow && !isColumnStyleDimDefined && defined(height) && heightMode == .stretchFit
      && stretch && childHeightMode != .stretchFit {
      childHeight = height; childHeightMode = .stretchFit
    }
    constrainMaxSizeForMode(child, row: true, &childWidthMode, &childWidth)
    constrainMaxSizeForMode(child, row: false, &childHeightMode, &childHeight)
    calculateLayoutInternal(child, childWidth, childHeight, childWidthMode, childHeightMode,
                            ownerWidth, ownerHeight, performLayout: false)
    child.computedFlexBasis = maxOrDefined(child.measured(row: isMainAxisRow), child.paddingAndBorder(row: isMainAxisRow))
  }
}

func measureNodeWithMeasureFunc(_ node: FlexNode, _ availableWidth: Float, _ availableHeight: Float,
                                _ widthMode: SizingMode, _ heightMode: SizingMode) {
  let availableWidth = widthMode == .maxContent ? Float.nan : availableWidth
  let availableHeight = heightMode == .maxContent ? Float.nan : availableHeight
  let pbRow = node.paddingAndBorder(row: true), pbColumn = node.paddingAndBorder(row: false)
  let innerWidth = availableWidth.isNaN ? availableWidth : maxOrDefined(0, availableWidth - pbRow)
  let innerHeight = availableHeight.isNaN ? availableHeight : maxOrDefined(0, availableHeight - pbColumn)
  if widthMode == .stretchFit && heightMode == .stretchFit {
    node.measuredWidth = boundAxis(node, row: true, availableWidth)
    node.measuredHeight = boundAxis(node, row: false, availableHeight)
  } else {
    let size = node.measure!(innerWidth, widthMode.measureMode, innerHeight, heightMode.measureMode)
    node.measuredWidth = boundAxis(node, row: true, widthMode == .stretchFit ? availableWidth : size.width + pbRow)
    node.measuredHeight = boundAxis(node, row: false, heightMode == .stretchFit ? availableHeight : size.height + pbColumn)
  }
}

func measureNodeWithoutChildren(_ node: FlexNode, _ availableWidth: Float, _ availableHeight: Float,
                                _ widthMode: SizingMode, _ heightMode: SizingMode) {
  let w = widthMode == .stretchFit ? availableWidth : node.paddingAndBorder(row: true)
  let h = heightMode == .stretchFit ? availableHeight : node.paddingAndBorder(row: false)
  node.measuredWidth = boundAxis(node, row: true, w)
  node.measuredHeight = boundAxis(node, row: false, h)
}

func measureNodeWithFixedSize(_ node: FlexNode, _ availableWidth: Float, _ availableHeight: Float,
                              _ widthMode: SizingMode, _ heightMode: SizingMode) -> Bool {
  guard (defined(availableWidth) && widthMode == .fitContent && availableWidth <= 0)
    || (defined(availableHeight) && heightMode == .fitContent && availableHeight <= 0)
    || (widthMode == .stretchFit && heightMode == .stretchFit) else { return false }
  node.measuredWidth = boundAxis(node, row: true,
    availableWidth.isNaN || (widthMode == .fitContent && availableWidth < 0) ? 0 : availableWidth)
  node.measuredHeight = boundAxis(node, row: false,
    availableHeight.isNaN || (heightMode == .fitContent && availableHeight < 0) ? 0 : availableHeight)
  return true
}

func calculateAvailableInnerDimension(_ node: FlexNode, row: Bool, _ availableDim: Float, _ paddingAndBorder: Float) -> Float {
  var inner = availableDim - paddingAndBorder
  if defined(inner) {
    let mn = node.minDim(row: row), mx = node.maxDim(row: row)
    let minInner: Float = mn.isNaN ? 0 : mn - paddingAndBorder
    let maxInner: Float = mx.isNaN ? .greatestFiniteMagnitude : mx - paddingAndBorder
    inner = maxOrDefined(minOrDefined(inner, maxInner), minInner)
  }
  return inner
}

func computeFlexBasisForChildren(_ node: FlexNode, _ availableInnerWidth: Float, _ availableInnerHeight: Float,
                                 _ widthMode: SizingMode, _ heightMode: SizingMode, performLayout: Bool) -> Float {
  let isMainAxisRow = node.direction == .row
  var singleFlexChild: FlexNode?
  if (isMainAxisRow ? widthMode : heightMode) == .stretchFit {
    for child in node.children where child.isNodeFlexible {
      if singleFlexChild != nil || abs(child.resolveFlexGrow()) < 0.0001 || abs(child.resolveFlexShrink()) < 0.0001 {
        singleFlexChild = nil; break
      }
      singleFlexChild = child
    }
  }
  var total: Float = 0
  for child in node.children {
    child.resolveDimension()
    if performLayout { child.setInitialPosition() }
    if child.isAbsolute { continue }
    if child === singleFlexChild {
      child.computedFlexBasis = 0
    } else {
      computeFlexBasisForChild(node, child, availableInnerWidth, widthMode, availableInnerHeight,
                               availableInnerWidth, availableInnerHeight, heightMode)
    }
    total += child.computedFlexBasis + child.margin(row: isMainAxisRow)
  }
  return total
}

/// FlexLine.cpp, single line (no wrap).
struct FlexLine {
  var sizeConsumed: Float = 0
  var totalFlexGrowFactors: Float = 0
  var totalFlexShrinkScaledFactors: Float = 0
  var remainingFreeSpace: Float = 0
  var mainDim: Float = 0
  var crossDim: Float = 0
  var itemsInFlow: [FlexNode] = []
}

func calculateFlexLine(_ node: FlexNode) -> FlexLine {
  let row = node.direction == .row
  let gap = node.gap(row: row)
  var line = FlexLine()
  for child in node.children where !child.isAbsolute {
    line.sizeConsumed += boundAxisWithinMinAndMax(child, row: row, child.computedFlexBasis) + child.margin(row: row)
      + (line.itemsInFlow.isEmpty ? 0 : gap)
    line.itemsInFlow.append(child)
    if child.isNodeFlexible {
      line.totalFlexGrowFactors += child.resolveFlexGrow()
      line.totalFlexShrinkScaledFactors += -child.resolveFlexShrink() * child.computedFlexBasis
    }
  }
  if line.totalFlexGrowFactors > 0 && line.totalFlexGrowFactors < 1 { line.totalFlexGrowFactors = 1 }
  if line.totalFlexShrinkScaledFactors > 0 && line.totalFlexShrinkScaledFactors < 1 { line.totalFlexShrinkScaledFactors = 1 }
  return line
}

func distributeFreeSpaceFirstPass(_ line: inout FlexLine, _ node: FlexNode) {
  let row = node.direction == .row
  var deltaFreeSpace: Float = 0
  for child in line.itemsInFlow {
    let childFlexBasis = boundAxisWithinMinAndMax(child, row: row, child.computedFlexBasis)
    if line.remainingFreeSpace < 0 {
      let shrinkScaled = -child.resolveFlexShrink() * childFlexBasis
      if defined(shrinkScaled) && shrinkScaled != 0 {
        let base = childFlexBasis + line.remainingFreeSpace / line.totalFlexShrinkScaledFactors * shrinkScaled
        let bound = boundAxis(child, row: row, base)
        if defined(base) && defined(bound) && base != bound {
          deltaFreeSpace += bound - childFlexBasis
          line.totalFlexShrinkScaledFactors -= -child.resolveFlexShrink() * child.computedFlexBasis
        }
      }
    } else if defined(line.remainingFreeSpace) && line.remainingFreeSpace > 0 {
      let grow = child.resolveFlexGrow()
      if defined(grow) && grow != 0 {
        let base = childFlexBasis + line.remainingFreeSpace / line.totalFlexGrowFactors * grow
        let bound = boundAxis(child, row: row, base)
        if defined(base) && defined(bound) && base != bound {
          deltaFreeSpace += bound - childFlexBasis
          line.totalFlexGrowFactors -= grow
        }
      }
    }
  }
  line.remainingFreeSpace -= deltaFreeSpace
}

func distributeFreeSpaceSecondPass(_ line: inout FlexLine, _ node: FlexNode, _ availableInnerMainDim: Float,
                                   _ availableInnerCrossDim: Float, _ availableInnerWidth: Float,
                                   _ availableInnerHeight: Float, _ crossMode: SizingMode, performLayout: Bool) -> Float {
  let row = node.direction == .row
  var deltaFreeSpace: Float = 0
  for child in line.itemsInFlow {
    let childFlexBasis = boundAxisWithinMinAndMax(child, row: row, child.computedFlexBasis)
    var updatedMainSize = childFlexBasis
    if defined(line.remainingFreeSpace) && line.remainingFreeSpace < 0 {
      let shrinkScaled = -child.resolveFlexShrink() * childFlexBasis
      if shrinkScaled != 0 {
        let childSize = defined(line.totalFlexShrinkScaledFactors) && line.totalFlexShrinkScaledFactors == 0
          ? childFlexBasis + shrinkScaled
          : childFlexBasis + (line.remainingFreeSpace / line.totalFlexShrinkScaledFactors) * shrinkScaled
        updatedMainSize = boundAxis(child, row: row, childSize)
      }
    } else if defined(line.remainingFreeSpace) && line.remainingFreeSpace > 0 {
      let grow = child.resolveFlexGrow()
      if !grow.isNaN && grow != 0 {
        updatedMainSize = boundAxis(child, row: row,
          childFlexBasis + line.remainingFreeSpace / line.totalFlexGrowFactors * grow)
      }
    }
    deltaFreeSpace += updatedMainSize - childFlexBasis

    var childMainSize = updatedMainSize + child.margin(row: row), childCrossSize = Float.nan
    var childMainMode = SizingMode.stretchFit, childCrossMode: SizingMode
    let stretch = resolveChildAlignment(node, child) == .stretch
    let crossDefinite = child.hasDefiniteLength(row: !row)
    if !availableInnerCrossDim.isNaN && !crossDefinite && crossMode == .stretchFit && stretch {
      childCrossSize = availableInnerCrossDim; childCrossMode = .stretchFit
    } else if !crossDefinite {
      childCrossSize = availableInnerCrossDim
      childCrossMode = childCrossSize.isNaN ? .maxContent : .fitContent
    } else {
      childCrossSize = child.resolved(row: !row) + child.margin(row: !row)
      childCrossMode = childCrossSize.isNaN ? .maxContent : .stretchFit
    }
    constrainMaxSizeForMode(child, row: row, &childMainMode, &childMainSize)
    constrainMaxSizeForMode(child, row: !row, &childCrossMode, &childCrossSize)
    let requiresStretchLayout = !crossDefinite && stretch
    calculateLayoutInternal(child,
      row ? childMainSize : childCrossSize, row ? childCrossSize : childMainSize,
      row ? childMainMode : childCrossMode, row ? childCrossMode : childMainMode,
      availableInnerWidth, availableInnerHeight, performLayout: performLayout && !requiresStretchLayout)
  }
  return deltaFreeSpace
}

func resolveFlexibleLength(_ line: inout FlexLine, _ node: FlexNode, _ availableInnerMainDim: Float,
                           _ availableInnerCrossDim: Float, _ availableInnerWidth: Float,
                           _ availableInnerHeight: Float, _ crossMode: SizingMode, performLayout: Bool) {
  let originalFreeSpace = line.remainingFreeSpace
  distributeFreeSpaceFirstPass(&line, node)
  let distributed = distributeFreeSpaceSecondPass(&line, node, availableInnerMainDim, availableInnerCrossDim,
    availableInnerWidth, availableInnerHeight, crossMode, performLayout: performLayout)
  line.remainingFreeSpace = originalFreeSpace - distributed
}

func justifyMainAxis(_ line: inout FlexLine, _ node: FlexNode, _ mainMode: SizingMode, _ crossMode: SizingMode,
                     _ availableInnerMainDim: Float, _ availableInnerCrossDim: Float, performLayout: Bool) {
  let row = node.direction == .row
  let leadingPB = node.flexStartPadding(row: row), trailingPB = node.flexEndPadding(row: row)
  if mainMode == .fitContent && line.remainingFreeSpace > 0 {
    let mn = node.minDim(row: row)
    if defined(mn) {
      let occupied = availableInnerMainDim - line.remainingFreeSpace
      line.remainingFreeSpace = maxOrDefined(0, mn - leadingPB - trailingPB - occupied)
    } else {
      line.remainingFreeSpace = 0
    }
  }
  var leadingMainDim: Float = 0
  var betweenMainDim = node.gap(row: row)
  var justify = node.justify
  if line.remainingFreeSpace < 0, [.spaceBetween, .spaceAround, .spaceEvenly].contains(justify) { justify = .flexStart }
  let n = Float(line.itemsInFlow.count)
  switch justify {
  case .center: leadingMainDim = line.remainingFreeSpace / 2
  case .flexEnd: leadingMainDim = line.remainingFreeSpace
  case .spaceBetween: if line.itemsInFlow.count > 1 { betweenMainDim += line.remainingFreeSpace / (n - 1) }
  case .spaceEvenly: leadingMainDim = line.remainingFreeSpace / (n + 1); betweenMainDim += leadingMainDim
  case .spaceAround: leadingMainDim = 0.5 * line.remainingFreeSpace / n; betweenMainDim += leadingMainDim * 2
  case .flexStart: break
  }
  line.mainDim = leadingPB + leadingMainDim
  line.crossDim = 0
  let canSkipFlex = !performLayout && crossMode == .stretchFit
  for child in node.children {
    if child.isAbsolute {
      guard performLayout else { continue }
      if let inset = child.leadingInset(row: row) {
        child.setPosition(inset + child.leadingMargin(row: row), row: row)
      } else {
        child.setPosition(child.position(row: row) + leadingMainDim, row: row)
      }
      continue
    }
    if performLayout { child.setPosition(child.position(row: row) + line.mainDim, row: row) }
    if child !== line.itemsInFlow.last { line.mainDim += betweenMainDim }
    if canSkipFlex {
      line.mainDim += child.margin(row: row) + child.computedFlexBasis
      line.crossDim = availableInnerCrossDim
    } else {
      line.mainDim += child.dimensionWithMargin(row: row)
      line.crossDim = maxOrDefined(line.crossDim, child.dimensionWithMargin(row: !row))
    }
  }
  line.mainDim += trailingPB
}

func calculateLayoutImpl(_ node: FlexNode, _ availableWidth: Float, _ availableHeight: Float,
                         _ widthMode: SizingMode, _ heightMode: SizingMode,
                         _ ownerWidth: Float, _ ownerHeight: Float, _ performLayout: Bool) {
  let marginAxisRow = node.margin(row: true), marginAxisColumn = node.margin(row: false)
  let availableWidth = availableWidth - marginAxisRow, availableHeight = availableHeight - marginAxisColumn
  if node.measure != nil {
    measureNodeWithMeasureFunc(node, availableWidth, availableHeight, widthMode, heightMode); return
  }
  if node.children.isEmpty {
    measureNodeWithoutChildren(node, availableWidth, availableHeight, widthMode, heightMode); return
  }
  if !performLayout && measureNodeWithFixedSize(node, availableWidth, availableHeight, widthMode, heightMode) { return }

  let row = node.direction == .row
  let pbCross = node.paddingAndBorder(row: !row)
  let leadingPBCross = node.flexStartPadding(row: !row)
  let mainMode = row ? widthMode : heightMode, crossMode = row ? heightMode : widthMode
  let pbRow = node.paddingAndBorder(row: true), pbColumn = node.paddingAndBorder(row: false)
  let availableInnerWidth = calculateAvailableInnerDimension(node, row: true, availableWidth, pbRow)
  let availableInnerHeight = calculateAvailableInnerDimension(node, row: false, availableHeight, pbColumn)
  var availableInnerMainDim = row ? availableInnerWidth : availableInnerHeight
  let availableInnerCrossDim = row ? availableInnerHeight : availableInnerWidth

  _ = computeFlexBasisForChildren(node, availableInnerWidth, availableInnerHeight, widthMode, heightMode,
                                  performLayout: performLayout)

  var line = calculateFlexLine(node)
  let canSkipFlex = !performLayout && crossMode == .stretchFit
  // RN runs with Errata::StretchFlexBasis (legacy stretch): sizeBasedOnContent stays false and a FitContent
  // main axis keeps its available size, so grow children fill it.
  if mainMode != .stretchFit {
    let minInnerMain = node.minDim(row: row) - (row ? pbRow : pbColumn)
    let maxInnerMain = node.maxDim(row: row) - (row ? pbRow : pbColumn)
    if defined(minInnerMain) && line.sizeConsumed < minInnerMain { availableInnerMainDim = minInnerMain }
    else if defined(maxInnerMain) && line.sizeConsumed > maxInnerMain { availableInnerMainDim = maxInnerMain }
  }
  if defined(availableInnerMainDim) { line.remainingFreeSpace = availableInnerMainDim - line.sizeConsumed }
  else if line.sizeConsumed < 0 { line.remainingFreeSpace = -line.sizeConsumed }

  if !canSkipFlex {
    resolveFlexibleLength(&line, node, availableInnerMainDim, availableInnerCrossDim, availableInnerWidth,
                          availableInnerHeight, crossMode, performLayout: performLayout)
  }
  justifyMainAxis(&line, node, mainMode, crossMode, availableInnerMainDim, availableInnerCrossDim,
                  performLayout: performLayout)

  var containerCrossAxis = availableInnerCrossDim
  if crossMode == .maxContent || crossMode == .fitContent {
    containerCrossAxis = boundAxis(node, row: !row, line.crossDim + pbCross) - pbCross
  }
  if crossMode == .stretchFit { line.crossDim = availableInnerCrossDim }
  line.crossDim = boundAxis(node, row: !row, line.crossDim + pbCross) - pbCross

  if performLayout {
    for child in node.children {
      if child.isAbsolute {
        child.setPosition((child.leadingInset(row: !row) ?? 0) + child.leadingMargin(row: !row), row: !row)
        continue
      }
      var leadingCrossDim = leadingPBCross
      let align = resolveChildAlignment(node, child)
      if align == .stretch {
        if !child.hasDefiniteLength(row: !row) {
          var childMainSize = child.measured(row: row) + child.margin(row: row)
          var childCrossSize = line.crossDim
          var mainM = SizingMode.stretchFit, crossM = SizingMode.stretchFit
          constrainMaxSizeForMode(child, row: row, &mainM, &childMainSize)
          constrainMaxSizeForMode(child, row: !row, &crossM, &childCrossSize)
          let w = row ? childMainSize : childCrossSize, h = row ? childCrossSize : childMainSize
          calculateLayoutInternal(child, w, h, w.isNaN ? .maxContent : .stretchFit, h.isNaN ? .maxContent : .stretchFit,
                                  availableInnerWidth, availableInnerHeight, performLayout: true)
        }
      } else {
        let remainingCrossDim = containerCrossAxis - child.dimensionWithMargin(row: !row)
        switch align {
        case .flexStart: break
        case .center: leadingCrossDim += remainingCrossDim / 2
        default: leadingCrossDim += remainingCrossDim
        }
      }
      child.setPosition((row ? child.top : child.left) + leadingCrossDim, row: !row)
    }
  }
  let totalLineCrossDim = line.crossDim
  let maxLineMainDim = maxOrDefined(0, line.mainDim)

  node.measuredWidth = boundAxis(node, row: true, availableWidth)
  node.measuredHeight = boundAxis(node, row: false, availableHeight)
  if mainMode == .maxContent || mainMode == .fitContent {
    node.setMeasured(boundAxis(node, row: row, maxLineMainDim), row: row)
  }
  if crossMode == .maxContent || crossMode == .fitContent {
    node.setMeasured(boundAxis(node, row: !row, totalLineCrossDim + pbCross), row: !row)
  }
  if performLayout {
    // STEP 11, layoutAbsoluteDescendants: every RN node is a containing block, so only direct children.
    for child in node.children where child.isAbsolute {
      layoutAbsoluteChild(node, child, availableInnerWidth, availableInnerHeight, row ? mainMode : crossMode)
    }
  }
}

// MARK: - AbsoluteLayout.cpp

func layoutAbsoluteChild(_ node: FlexNode, _ child: FlexNode, _ containingBlockWidth: Float,
                         _ containingBlockHeight: Float, _ widthMode: SizingMode) {
  let isMainAxisRow = node.direction == .row
  var childWidth = Float.nan, childHeight = Float.nan
  let marginRow = child.margin(row: true), marginColumn = child.margin(row: false)
  for r in [true, false] {
    var size = Float.nan
    if child.hasDefiniteLength(row: r) {
      size = child.resolved(row: r) + child.margin(row: r)
    } else if let lead = child.leadingInset(row: r), let trail = child.trailingInset(row: r) {
      size = boundAxis(child, row: r, node.measured(row: r) - (lead + trail))
    }
    if r { childWidth = size } else { childHeight = size }
  }
  if childWidth.isNaN || childHeight.isNaN {
    var wMode: SizingMode = childWidth.isNaN ? .maxContent : .stretchFit
    let hMode: SizingMode = childHeight.isNaN ? .maxContent : .stretchFit
    if !isMainAxisRow && childWidth.isNaN && widthMode != .maxContent
      && defined(containingBlockWidth) && containingBlockWidth > 0 {
      childWidth = containingBlockWidth; wMode = .fitContent
    }
    calculateLayoutInternal(child, childWidth, childHeight, wMode, hMode,
                            containingBlockWidth, containingBlockHeight, performLayout: false)
    childWidth = child.measuredWidth + marginRow
    childHeight = child.measuredHeight + marginColumn
  }
  calculateLayoutInternal(child, childWidth, childHeight, .stretchFit, .stretchFit,
                          containingBlockWidth, containingBlockHeight, performLayout: true)
  positionAbsoluteChildLegacy(node, child, row: isMainAxisRow, isMainAxis: true)
  positionAbsoluteChildLegacy(node, child, row: !isMainAxisRow, isMainAxis: false)
}

/// Errata AbsolutePositioningIncorrect: insets-less children ignore padding, centring uses the whole box.
func positionAbsoluteChildLegacy(_ node: FlexNode, _ child: FlexNode, row: Bool, isMainAxis: Bool) {
  let align = resolveChildAlignment(node, child)
  let shouldCenter = isMainAxis ? node.justify == .center : align == .center
  let shouldFlexEnd = isMainAxis ? node.justify == .flexEnd : align == .flexEnd
  let hasLeading = child.leadingInset(row: row) != nil
  if let trail = child.trailingInset(row: row), !hasLeading {
    child.setPosition(node.measured(row: row) - child.measured(row: row) - child.trailingMargin(row: row) - trail, row: row)
  } else if !hasLeading && shouldCenter {
    child.setPosition((node.measured(row: row) - child.measured(row: row)) / 2, row: row)
  } else if !hasLeading && shouldFlexEnd {
    child.setPosition(node.measured(row: row) - child.measured(row: row), row: row)
  }
}
