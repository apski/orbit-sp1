// UIKit back-end: Spectra IR -> a UIView subclass built from UIStackView + Auto Layout, no FlexLayout.
// Layout decisions come from ../stacks.mjs (shared with the SwiftUI back-end). Stack mapping: axis/alignment/spacing,
// padding -> directionalLayoutMargins, margins -> custom spacing (main axis) or a margin wrapper (cross axis), grow ->
// lowest hugging priority, justify -> spacer views. Whatever UIStackView can't express is listed as unlowered.
// With probes on (default) the file also has a SwiftUI `struct <Name>: View { init(onFrames:) }` that hosts the view
// and reports every IR node's frame plus ambiguous-layout counts (helper: preview/Sources/SpectraProbe.swift).
// CLI: node lower.mjs <ir.json> <Out.generated.swift> [--ignore-safe-area all|bottom|none] [--no-probes]
import { readFileSync, writeFileSync } from 'node:fs';
import { basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { plan, coverage } from '../stacks.mjs';
import { parseColor, weightName, SYSTEM_FONT } from '../swiftui/paint.mjs';

const camel = (s, fallback) => {
  const w = String(s).split(/[^A-Za-z0-9]+/).filter(Boolean).slice(0, 4);
  if (!w.length) return fallback;
  const out = w.map((x, i) => (i ? x[0].toUpperCase() + x.slice(1) : x[0].toLowerCase() + x.slice(1))).join('');
  return /^[0-9]/.test(out) ? `v${out}` : out;
};
const add = (a, b) => ({ t: a.t + b.t, l: a.l + b.l, b: a.b + b.b, r: a.r + b.r });
const ZERO = { t: 0, l: 0, b: 0, r: 0 };
const insets = (e, T = 'NSDirectionalEdgeInsets') => (T === 'UIEdgeInsets'
  ? `UIEdgeInsets(top: ${e.t}, left: ${e.l}, bottom: ${e.b}, right: ${e.r})`
  : `NSDirectionalEdgeInsets(top: ${e.t}, leading: ${e.l}, bottom: ${e.b}, trailing: ${e.r})`);

const IGNORE = { all: '.ignoresSafeArea()', bottom: '.ignoresSafeArea(edges: .bottom)', none: '' };

export function generate(input, { name, ignoreEdges = 'all', sourceName = 'ir', probes = true } = {}) {
  const { ir, root, nodes } = plan(input);
  const gaps = new Set(), local = [];
  const props = [], setup = [], constraints = [], capsules = [], probeList = [];
  const used = new Set();

  function color(c) {
    const rgba = parseColor(c);
    if (!rgba) { gaps.add(`color ${JSON.stringify(c)}`); return '.black'; }
    const f = v => +(v / 255).toFixed(4);
    return `UIColor(red: ${f(rgba[0])}, green: ${f(rgba[1])}, blue: ${f(rgba[2])}, alpha: ${+(+rgba[3]).toFixed(4)})`;
  }
  function declare(base, type) {
    let n = base;
    for (let k = 2; used.has(n); k++) n = `${base}${k}`;
    used.add(n);
    props.push(`  private let ${n} = ${type}()`);
    return n;
  }
  const effMargin = p => (p.kind === 'pass' ? add(p.margin, effMargin(p.child)) : p.margin);
  const leaf = p => (p.kind === 'pass' ? leaf(p.child) : p.view);

  /// Background, corners, shadow, opacity, identifier: the same for every kind of view.
  function decorate(v, p, s) {
    if (s.backgroundColor && s.backgroundColor !== 'transparent') setup.push(`${v}.backgroundColor = ${color(s.backgroundColor)}`);
    if (s.borderRadius >= 999) capsules.push(v);
    else if (s.borderRadius) setup.push(`${v}.layer.cornerRadius = ${s.borderRadius}`, `${v}.layer.cornerCurve = .continuous`);
    if (s.shadowColor && (s.shadowOpacity ?? 1) > 0) {
      const o = s.shadowOffset ?? { width: 0, height: 0 };
      setup.push(`${v}.layer.shadowColor = ${color(s.shadowColor)}.cgColor`, `${v}.layer.shadowOpacity = ${s.shadowOpacity ?? 1}`,
        `${v}.layer.shadowRadius = ${s.shadowRadius ?? 0}`, `${v}.layer.shadowOffset = CGSize(width: ${o.width}, height: ${o.height})`);
    }
    if (s.outlineWidth) gaps.add(`outline (#${p.i})`);
    if (s.opacity !== undefined && s.opacity !== 1) setup.push(`${v}.alpha = ${s.opacity}`);
    if (s.overflow === 'hidden' && p.kind !== 'text') setup.push(`${v}.clipsToBounds = true`);
  }

  function label(p) {
    const s = p.s, n = p.n;
    const v = declare(`${camel(n.text, 'text')}Label`, 'UILabel');
    const size = s.fontSize ?? 14, w = weightName(s.fontWeight);
    let font = `.systemFont(ofSize: ${size}, weight: .${w})`;
    if (s.fontFamily && !SYSTEM_FONT.test(s.fontFamily)) {
      gaps.add(`font "${s.fontFamily}" must be bundled in the app (falls back to the system font if missing)`);
      font = `UIFont(name: ${JSON.stringify(s.fontFamily)}, size: ${size}) ?? ${font}`;
    }
    if (s.fontStyle === 'italic') gaps.add(`italic (#${p.i})`);
    const align = { center: '.center', right: '.right' }[s.textAlign];
    setup.push(`${v}.numberOfLines = ${n.numberOfLines ?? 0}`);
    if (s.lineHeight || s.letterSpacing) {
      // Fixed line height like RN: every line is exactly lineHeight tall, glyphs centred in it.
      setup.push(`${v}.attributedText = .styled(${JSON.stringify(n.text ?? '')}, font: ${font}, color: ${color(s.color ?? 'black')}, lineHeight: ${s.lineHeight ?? 'nil'}, kern: ${s.letterSpacing ?? 0}, alignment: ${align ?? '.natural'})`);
      needStyled = true;
    } else {
      setup.push(`${v}.text = ${JSON.stringify(n.text ?? '')}`, `${v}.font = ${font}`, `${v}.textColor = ${color(s.color ?? 'black')}`);
      if (align) setup.push(`${v}.textAlignment = ${align}`);
    }
    return v;
  }
  let needStyled = false;

  /// Builds the view for a plan node; returns its name (pass-throughs return their child's).
  function build(p) {
    const s = p.s;
    if (p.kind === 'pass') {
      p.view = build(p.child);
      if (p.n.testID) setup.push(`${p.view}.accessibilityIdentifier = ${JSON.stringify(p.n.testID)}`);
      return p.view;
    }
    let v;
    if (p.kind === 'text') v = label(p);
    else if (p.kind === 'box' || p.kind === 'image') {
      v = declare(p.kind === 'box' ? 'box' : 'imageView', p.kind === 'box' ? 'UIView' : 'UIImageView');
      if (p.kind === 'image') { gaps.add(`image ${JSON.stringify(p.n.source ?? '')} (#${p.i}, placeholder)`); setup.push(`${v}.backgroundColor = UIColor.gray.withAlphaComponent(0.2)`); }
      // A leaf box is its padding unless stretched or sized.
      if (p.width === undefined && !p.sx) constraints.push(`${v}.widthAnchor.constraint(equalToConstant: ${p.pad.l + p.pad.r})`);
      if (p.height === undefined && !p.sy) constraints.push(`${v}.heightAnchor.constraint(equalToConstant: ${p.pad.t + p.pad.b})`);
    } else if (p.kind === 'scroll') {
      v = declare('scrollView', 'UIScrollView');
      const cib = { automatic: '.automatic', always: '.always', scrollableAxes: '.scrollableAxes' }[p.n.contentInsetAdjustmentBehavior] ?? '.never';
      if (cib !== '.automatic') setup.push(`${v}.contentInsetAdjustmentBehavior = ${cib}`);
      // RN's default (alwaysBounceVertical = !horizontal). It also makes .automatic inset short content, as RN does.
      if (p.n.alwaysBounceVertical !== false && p.n.alwaysBounceHorizontal !== false) setup.push(`${v}.alwaysBounce${p.horizontal ? 'Horizontal' : 'Vertical'} = true`);
      if (p.child) {
        const c = build(p.child), m = add(p.pad, effMargin(p.child));
        setup.push(`${v}.addSubview(${c})`, `${c}.translatesAutoresizingMaskIntoConstraints = false`);
        const g = `${v}.contentLayoutGuide`;
        constraints.push(`${c}.topAnchor.constraint(equalTo: ${g}.topAnchor, constant: ${m.t})`,
          `${c}.leadingAnchor.constraint(equalTo: ${g}.leadingAnchor, constant: ${m.l})`,
          `${c}.bottomAnchor.constraint(equalTo: ${g}.bottomAnchor, constant: ${-m.b})`,
          `${c}.trailingAnchor.constraint(equalTo: ${g}.trailingAnchor, constant: ${-m.r})`,
          p.horizontal ? `${c}.heightAnchor.constraint(equalTo: ${v}.frameLayoutGuide.heightAnchor, constant: ${-(m.t + m.b)})`
            : `${c}.widthAnchor.constraint(equalTo: ${v}.frameLayoutGuide.widthAnchor, constant: ${-(m.l + m.r)})`);
      }
    } else v = stack(p);
    p.view = v;
    if (p.width !== undefined) constraints.push(`${v}.widthAnchor.constraint(equalToConstant: ${p.width})`);
    if (p.height !== undefined) constraints.push(`${v}.heightAnchor.constraint(equalToConstant: ${p.height})`);
    decorate(v, p, s);
    if (p.n.testID) setup.push(`${v}.accessibilityIdentifier = ${JSON.stringify(p.n.testID)}`);
    return v;
  }

  function stack(p) {
    const v = declare(p.n.testID ? `${camel(p.n.testID, 'stack')}Stack` : 'stack', 'UIStackView');
    const vert = p.axis === 'v';
    const [ms, me, ca, cb] = vert ? ['t', 'b', 'l', 'r'] : ['l', 'r', 't', 'b'];
    const ALIGN = vert ? { start: '.leading', center: '.center', end: '.trailing' } : { start: '.top', center: '.center', end: '.bottom' };
    const align = p.align === 'stretch' ? '.fill' : p.align === 'baseline' && !vert ? '.firstBaseline' : ALIGN[vert ? p.content.h : p.content.v];
    if (vert) setup.push(`${v}.axis = .vertical`);
    if (align !== '.fill') setup.push(`${v}.alignment = ${align}`);
    if (p.spacers === 'between') setup.push(`${v}.distribution = .equalSpacing`);
    if (p.equal) setup.push(`${v}.distribution = .fillEqually`);
    if (p.gap) setup.push(`${v}.spacing = ${p.gap}`);

    const margins = { ...p.pad };
    for (const c of p.children.filter(c => c.absolute)) {
      // Absolute: a plain subview pinned by its insets to the stack's bounds (Yoga: the parent's border box).
      const a = c.absolute, view = build(c);
      setup.push(`${v}.addSubview(${view})`, `${view}.translatesAutoresizingMaskIntoConstraints = false`);
      if (a.top !== undefined || a.bottom === undefined) constraints.push(`${view}.topAnchor.constraint(equalTo: ${v}.topAnchor, constant: ${a.top ?? 0})`);
      if (a.bottom !== undefined) constraints.push(`${view}.bottomAnchor.constraint(equalTo: ${v}.bottomAnchor, constant: ${-a.bottom})`);
      if (a.left !== undefined || a.right === undefined) constraints.push(`${view}.leadingAnchor.constraint(equalTo: ${v}.leadingAnchor, constant: ${a.left ?? 0})`);
      if (a.right !== undefined) constraints.push(`${view}.trailingAnchor.constraint(equalTo: ${v}.trailingAnchor, constant: ${-a.right})`);
    }
    const stretchers = [];
    const units = p.children.filter(c => !c.absolute).map(c => {
      const m = effMargin(c);
      const stretchOne = c.alignSelf === 'stretch' && p.align !== 'stretch';
      if (c.alignSelf !== p.align && !stretchOne) local.push(`#${c.i}: alignSelf ${c.alignSelf} (UIStackView aligns all children alike)`);
      if (p.align === 'stretch' && (vert ? c.width : c.height) !== undefined) local.push(`#${c.i}: fixed cross size in a stretching stack`);
      let view = build(c);
      if (p.children.length === 1) { margins[ca] += m[ca]; margins[cb] += m[cb]; }
      else if (m[ca] || m[cb]) {
        // Cross-axis margins: UIStackView has none, so a plain wrapper insets the child.
        const w = declare(`${view}Margin`, 'UIView');
        setup.push(`${w}.addSubview(${view})`, `${view}.translatesAutoresizingMaskIntoConstraints = false`);
        const e = vert ? { t: 0, b: 0, l: m.l, r: m.r } : { t: m.t, b: m.b, l: 0, r: 0 };
        constraints.push(`${view}.topAnchor.constraint(equalTo: ${w}.topAnchor, constant: ${e.t})`,
          `${view}.leadingAnchor.constraint(equalTo: ${w}.leadingAnchor, constant: ${e.l})`,
          `${view}.bottomAnchor.constraint(equalTo: ${w}.bottomAnchor, constant: ${-e.b})`,
          `${view}.trailingAnchor.constraint(equalTo: ${w}.trailingAnchor, constant: ${-e.r})`);
        view = w;
      }
      if (stretchOne && (vert ? c.sx : c.sy)) stretchers.push(view);
      if (vert ? c.sy : c.sx) setup.push(`${view}.setContentHuggingPriority(UILayoutPriority(1), for: .${vert ? 'vertical' : 'horizontal'})`);
      return { view, m };
    });

    const spacer = side => {
      const sp = declare(`${v}${side}Spacer`, 'UIView');
      setup.push(`${sp}.setContentHuggingPriority(UILayoutPriority(1), for: .${vert ? 'vertical' : 'horizontal'})`);
      return sp;
    };
    const first = units[0], last = units[units.length - 1];
    const lead = p.spacers === 'start' || p.spacers === 'both' ? spacer(vert ? 'Top' : 'Leading') : null;
    const trail = p.spacers === 'end' || p.spacers === 'both' ? spacer(vert ? 'Bottom' : 'Trailing') : null;
    if (lead && trail) constraints.push(`${lead}.${vert ? 'height' : 'width'}Anchor.constraint(equalTo: ${trail}.${vert ? 'height' : 'width'}Anchor)`);
    const custom = [];
    if (lead) { setup.push(`${v}.addArrangedSubview(${lead})`); custom.push([lead, first.m[ms]]); } else margins[ms] += first.m[ms];
    units.forEach((u, j) => {
      setup.push(`${v}.addArrangedSubview(${u.view})`);
      if (j < units.length - 1) custom.push([u.view, p.gap + u.m[me] + units[j + 1].m[ms]]);
    });
    if (trail) { setup.push(`${v}.addArrangedSubview(${trail})`); custom.push([last.view, last.m[me]]); } else margins[me] += last.m[me];
    for (const [after, sp] of custom) if (sp !== p.gap) setup.push(`${v}.setCustomSpacing(${sp}, after: ${after})`);
    // One child stretched in a non-filling stack: pin its cross size to the stack's content width/height.
    const dim = vert ? 'width' : 'height';
    for (const view of stretchers)
      constraints.push(`${view}.${dim}Anchor.constraint(equalTo: ${v}.${dim}Anchor, constant: ${-(margins[ca] + margins[cb])})`);
    if (margins.t || margins.l || margins.b || margins.r)
      // insetsLayoutMarginsFromSafeArea would add the safe area to RN's padding; RN never does.
      setup.push(`${v}.isLayoutMarginsRelativeArrangement = true`, `${v}.insetsLayoutMarginsFromSafeArea = false`,
        `${v}.directionalLayoutMargins = ${insets(margins)}`);
    return v;
  }

  const top = build(root);
  const rm = effMargin(root);
  constraints.unshift(`${top}.topAnchor.constraint(equalTo: topAnchor, constant: ${rm.t})`,
    `${top}.leadingAnchor.constraint(equalTo: leadingAnchor, constant: ${rm.l})`,
    `${top}.bottomAnchor.constraint(equalTo: bottomAnchor, constant: ${-rm.b})`,
    `${top}.trailingAnchor.constraint(equalTo: trailingAnchor, constant: ${-rm.r})`);

  // Probe per IR node (preorder): its view, outset by the margins of wrappers collapsed into it.
  (function collect(p) {
    probeList[p.i] = p.kind === 'pass' ? [leaf(p), effMargin(p.child)] : [p.view, ZERO];
    for (const c of p.children ?? (p.child ? [p.child] : [])) collect(c);
  })(root);

  const cov = coverage(root);
  cov.unlowered.push(...local);
  cov.lowered = cov.nodes - new Set(cov.unlowered.map(u => u.split(/[ :]/)[0])).size;
  const cls = probes ? `${name}View` : name;
  const swift = `// Generated by Spectra (backends/uikit/lower.mjs) from ${sourceName} (${ir.source ?? 'rn'} screen ${ir.screen}). Do not edit.
// UIStackView + Auto Layout: ${cov.lowered}/${cov.nodes} nodes lowered by rule, ${cov.pass} pass-through wrappers removed.
// Unlowered (needs a lowering rule or the agent):
${cov.unlowered.map(u => `//   - ${u}`).join('\n') || '//   none'}
// Gaps (not reproduced, reported instead of guessed):
${[...gaps].sort().map(g => `//   - ${g}`).join('\n') || '//   none'}
import UIKit
${probes ? 'import SwiftUI\n' : ''}
final class ${cls}: UIView${probes ? ', SpectraProbed' : ''} {
${props.join('\n')}

  override init(frame: CGRect) {
    super.init(frame: frame)
${setup.map(l => `    ${l}`).join('\n')}
    addSubview(${top})
    ${top}.translatesAutoresizingMaskIntoConstraints = false
    NSLayoutConstraint.activate([
${constraints.map(l => `      ${l},`).join('\n')}
    ])
  }

  required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }
${capsules.length ? `
  override func layoutSubviews() {
    super.layoutSubviews()
    for v in [${capsules.join(', ')}] as [UIView] { v.layer.cornerRadius = min(v.bounds.width, v.bounds.height) / 2 }
  }
` : ''}${probes ? `
  /// Every IR node's view in preorder, outset by collapsed wrappers' margins (parity judge).
  var probes: [(UIView, UIEdgeInsets)] {
    [${probeList.map(([pv, e]) => `(${pv}, ${e === ZERO ? '.zero' : insets(e, 'UIEdgeInsets')})`).join(', ')}]
  }
` : ''}}
${needStyled ? `
private extension NSAttributedString {
  /// RN-style text: fixed line height with the glyphs centred in each line.
  static func styled(_ s: String, font: UIFont, color: UIColor, lineHeight: CGFloat?, kern: CGFloat, alignment: NSTextAlignment) -> NSAttributedString {
    let p = NSMutableParagraphStyle()
    p.alignment = alignment
    var a: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color, .paragraphStyle: p, .kern: kern]
    if let lineHeight {
      p.minimumLineHeight = lineHeight
      p.maximumLineHeight = lineHeight
      a[.baselineOffset] = (lineHeight - font.lineHeight) / 4  // ponytail: the usual centring offset; ~1pt glyph drift is a known gap
    }
    return NSAttributedString(string: s, attributes: a)
  }
}
` : ''}${probes ? `
struct ${name}: View {
  var onFrames: ((Int, [CGRect]) -> Void)? = nil
  // UIKit takes safe-area insets from the window, so the host can cover it like the RN root did.
  var body: some View { SpectraUIKitHost(make: { ${cls}() }, onFrames: onFrames)${IGNORE[ignoreEdges]} }
}
` : ''}`;
  return { swift, map: Array.from({ length: nodes }, (_, i) => [0, i]), gaps: [...gaps].sort(), nodes, coverage: cov };
}

export function generateFile(inPath, outPath, opts = {}) {
  const name = opts.name ?? basename(outPath).replace(/\.generated\.swift$/, '').replace(/[^A-Za-z0-9]/g, '');
  const r = generate(JSON.parse(readFileSync(inPath, 'utf8')), { sourceName: basename(inPath), ...opts, name });
  writeFileSync(outPath, r.swift);
  writeFileSync(outPath.replace(/\.swift$/, '.map.json'), JSON.stringify({ capture: basename(inPath), map: r.map }));
  return r;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [inPath, outPath] = process.argv.slice(2);
  const ia = process.argv.indexOf('--ignore-safe-area');
  const r = generateFile(inPath, outPath, { ignoreEdges: ia > 0 ? process.argv[ia + 1] : 'all', probes: !process.argv.includes('--no-probes') });
  console.log(`${outPath}: ${r.coverage.lowered}/${r.nodes} lowered, ${r.coverage.pass} pass-through, ${r.gaps.length} gaps`);
}
