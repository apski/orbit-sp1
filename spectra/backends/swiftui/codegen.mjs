// SwiftUI back-end: Spectra IR -> one `<Name>.generated.swift` per screen, laid out by FlexLayout's Flex (a Swift port
// of RN's Yoga). Deterministic, no model. Every unsupported style is listed in the file header as a gap, never guessed.
// Also returns `map`: for each IR node (preorder) the [composition, Flex node] it became, for the judge.
// CLI: node codegen.mjs <ir.json> <Out.generated.swift> [--ignore-safe-area all|bottom|none]
import { readFileSync, writeFileSync } from 'node:fs';
import { basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalize } from '../../ir/ir.mjs';
import { paint } from './paint.mjs';

const LAYOUT_KEYS = ['flexDirection', 'justifyContent', 'alignItems', 'alignSelf', 'flexGrow', 'flexShrink', 'flex',
  'flexBasis', 'width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight', 'rowGap', 'columnGap', 'position',
  'top', 'right', 'bottom', 'left'];
const ENUM_KEYS = ['flexDirection', 'justifyContent', 'alignItems', 'alignSelf', 'position'];
const EDGES = { Top: 'Vertical', Bottom: 'Vertical', Left: 'Horizontal', Right: 'Horizontal' };
const TEXT_KEYS = new Set(['color', 'fontSize', 'fontWeight', 'fontFamily', 'lineHeight', 'textAlign', 'letterSpacing',
  'fontStyle', 'textDecorationLine', 'includeFontPadding', 'textAlignVertical']);
const PAINT_KEYS = new Set(['backgroundColor', 'borderRadius', 'opacity', 'zIndex', 'overflow', 'outlineColor',
  'outlineWidth', 'outlineOffset', 'shadowColor', 'shadowOffset', 'shadowRadius', 'shadowOpacity']);
const IGNORE = { all: '.ignoresSafeArea()', bottom: '.ignoresSafeArea(edges: .bottom)', none: '' };
export function generate(input, { name, ignoreEdges = 'all', sourceName = 'ir' } = {}) {
  const ir = normalize(input);
  const gaps = new Set();
  const compositions = [];
  const map = [];
  const { color, textView, decorationView } = paint(gaps);
  const win = ir.roots[0]?.frame ?? [0, 0, 0, 0];

  /// RN style -> FlexStyle (Yoga edge precedence: edge > axis > all; start/end are LTR left/right).
  function flexStyle(s, where) {
    const out = {};
    for (const k of LAYOUT_KEYS) {
      if (s[k] === undefined) continue;
      if (k === 'flexBasis' && s[k] === '0%') { out[k] = 0; continue; }  // flex-1; exact unless the parent's main size is undefined
      if (typeof s[k] === 'string' && !ENUM_KEYS.includes(k)) { gaps.add(`${k}: ${JSON.stringify(s[k])} (${where})`); continue; }
      out[k] = s[k];
    }
    if (s.start !== undefined) out.left ??= s.start;
    if (s.end !== undefined) out.right ??= s.end;
    if (s.gap !== undefined) { out.rowGap ??= s.gap; out.columnGap ??= s.gap; }
    for (const p of ['padding', 'margin']) for (const [e, axis] of Object.entries(EDGES)) {
      const alias = e === 'Left' ? s[`${p}Start`] : e === 'Right' ? s[`${p}End`] : undefined;
      const v = s[`${p}${e}`] ?? alias ?? s[`${p}${axis}`] ?? s[p];
      if (v === undefined) continue;
      if (typeof v !== 'number') { gaps.add(`${p}${e}: ${JSON.stringify(v)} (${where})`); continue; }
      out[`${p}${e}`] = v;
    }
    if (['flexWrap', 'aspectRatio', 'display'].some(k => s[k] !== undefined && s[k] !== 'nowrap' && s[k] !== 'flex'))
      gaps.add(`flexWrap/aspectRatio/display (${where})`);
    if (Object.keys(s).some(k => /^border.*Width$/.test(k) && s[k])) gaps.add(`border widths (${where})`);
    return out;
  }

  const z = s => (s.zIndex ? `.zIndex(${s.zIndex})` : '') + (s.opacity !== undefined && s.opacity !== 1 ? `.opacity(${s.opacity})` : '');

  let counter = 0;
  /// One Flex: returns its index. Node indices count the synthetic root as 0.
  function compose(rootStyle, rootSafeArea, children) {
    const c = { views: [], next: 1 };
    const id = compositions.push(c) - 1;
    c.spec = { style: rootStyle, ...(rootSafeArea ? { safeArea: rootSafeArea } : {}), children: children.map(n => spec(n, c, id)) };
    return id;
  }

  function spec(n, comp, compId) {
    const index = counter++;
    map[index] = [compId, comp.next++];
    const where = `${n.hostType ?? n.type}#${index}${n.testID ? ' ' + n.testID : ''}${n.name ? ' ' + JSON.stringify(n.name) : ''}`;
    if (n.hostType) gaps.add(`host view ${n.hostType} drawn as a plain view (${where})`);
    const s = n.style ?? {};
    const views = comp.views;
    const out = { style: flexStyle(s, where) };
    if (n.type === 'safe-area') out.safeArea = 'tblr';
    if (n.type === 'scroll') {
      // The scroll view is a leaf filling its frame; its content is a separate Flex, with the scroll view's padding.
      const inner = {}, pad = {};
      for (const [k, v] of Object.entries(out.style)) (k.startsWith('padding') ? pad : inner)[k] = v;
      out.style = inner;
      const deco = decorationView(s);
      if (deco) { out.decoration = views.length; views.push(deco); }
      let edges = '';
      if (n.contentInsetAdjustmentBehavior === 'automatic' || n.contentInsetAdjustmentBehavior === 'always') {
        if (n.frame?.[1] <= win[1]) edges += 't';
        if (n.frame && n.frame[1] + n.frame[3] >= win[1] + win[3] - 0.5) edges += 'b';
      }
      const content = n.children?.[0];
      const contentId = compose({ ...pad, ...(n.horizontal ? { flexDirection: 'row' } : {}) }, edges, content ? [content] : []);
      out.leaf = views.length;
      views.push(`ScrollView(${n.horizontal ? '.horizontal' : ''}) { ${flexCall(contentId)} }`);
      return out;
    }
    const deco = decorationView(s);
    if (deco) { out.decoration = views.length; views.push(deco + z(s)); }
    if (n.type === 'text') {
      out.leaf = views.length; views.push(textView(n) + z(s));
      if (s.textAlign === 'center' || s.textAlign === 'right') out.align = s.textAlign;
    } else if (n.type === 'image') {
      gaps.add(`image ${JSON.stringify(n.source ?? n.name ?? '')} (${where}, drawn as placeholder)`);
      out.leaf = views.length; views.push('Color.gray.opacity(0.2)');
    }
    for (const k of Object.keys(s)) if (!LAYOUT_KEYS.includes(k) && !TEXT_KEYS.has(k) && !PAINT_KEYS.has(k)
      && !/^(padding|margin|gap|start|end|border|shadow|elevation)/.test(k)) gaps.add(`style ${k} (${where})`);
    if (n.children?.length) out.children = n.children.map(c => spec(c, comp, compId));
    return out;
  }

  const flexCall = id => `Flex(Self.spec${id}, safeArea: safe.safeAreaInsets, onFrames: { onFrames?(${id}, $0) }) {\n${
    compositions[id].views.map(v => '        ' + v).join('\n')}\n      }`;

  // Synthetic root = the container the source laid the screen out in (stretches to what it is given).
  compose({}, '', ir.roots);
  const swift = `// Generated by Spectra (backends/swiftui/codegen.mjs) from ${sourceName} (${ir.source ?? 'rn'} screen ${ir.screen}). Do not edit.
// Gaps (not reproduced, reported instead of guessed):
${[...gaps].sort().map(g => `//   - ${g}`).join('\n') || '//   none'}
import SwiftUI
import FlexLayout

struct ${name}: View {
  /// (composition, every node frame in preorder), for the parity judge.
  var onFrames: ((Int, [CGRect]) -> Void)? = nil

  var body: some View {
    GeometryReader { safe in  // insets come from a reader inside the safe area; layout uses the full window
      GeometryReader { _ in
        ${flexCall(0)}
      }
      ${IGNORE[ignoreEdges]}
    }
  }
${compositions.map((c, i) => `
  static let spec${i} = FlexSpec.decode(#"""
${JSON.stringify(c.spec)}
"""#)`).join('\n')}
}
`;
  return { swift, map, gaps: [...gaps].sort(), compositions: compositions.length, nodes: counter };
}

export function generateFile(inPath, outPath, opts = {}) {
  const name = basename(outPath).replace(/\.generated\.swift$/, '').replace(/[^A-Za-z0-9]/g, '');
  const r = generate(JSON.parse(readFileSync(inPath, 'utf8')), { name, sourceName: basename(inPath), ...opts });
  writeFileSync(outPath, r.swift);
  writeFileSync(outPath.replace(/\.swift$/, '.map.json'), JSON.stringify({ capture: basename(inPath), map: r.map }));
  return r;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [inPath, outPath] = process.argv.slice(2);
  const ia = process.argv.indexOf('--ignore-safe-area');
  const r = generateFile(inPath, outPath, { ignoreEdges: ia > 0 ? process.argv[ia + 1] : 'all' });
  console.log(`${outPath}: ${r.compositions} compositions, ${r.nodes} nodes, ${r.gaps.length} gaps`);
}
