// SwiftUI native-stacks back-end: Spectra IR -> idiomatic SwiftUI (VStack/HStack, frames, padding), no FlexLayout.
// Layout decisions come from ../stacks.mjs (shared with the UIKit back-end); paint from ./paint.mjs (shared with
// codegen.mjs). Whatever stacks can't express is listed in the file header as unlowered, never guessed.
// With probes on (default), every IR node reports its frame for the judge: `.spectraFrame(i)`, helper in
// preview/Sources/SpectraProbe.swift. `--no-probes` emits the production view.
// CLI: node lower.mjs <ir.json> <Out.generated.swift> [--ignore-safe-area all|bottom|none] [--no-probes]
import { readFileSync, writeFileSync } from 'node:fs';
import { basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { plan, coverage } from '../stacks.mjs';
import { paint } from './paint.mjs';

const IGNORE = { all: '.ignoresSafeArea()', bottom: '.ignoresSafeArea(edges: .bottom)', none: '' };
const H = { start: 'leading', center: 'center', end: 'trailing' }, V = { start: 'top', center: 'center', end: 'bottom' };

/** SwiftUI Alignment name for content placement { h, v }. */
function alignment({ h, v }) {
  if (h === 'center' && v === 'center') return 'center';
  if (v === 'center') return H[h];
  if (h === 'center') return V[v];
  return V[v] + H[h][0].toUpperCase() + H[h].slice(1);
}

/** Padding with the fewest modifiers: one value, vertical/horizontal pairs, then single edges. */
function padding(e) {
  const { t, l, b, r } = e;
  if (!t && !l && !b && !r) return '';
  if (t === l && l === b && b === r) return `.padding(${t})`;
  let out = '';
  if (t === b) { if (t) out += `.padding(.vertical, ${t})`; } else { if (t) out += `.padding(.top, ${t})`; if (b) out += `.padding(.bottom, ${b})`; }
  if (l === r) { if (l) out += `.padding(.horizontal, ${l})`; } else { if (l) out += `.padding(.leading, ${l})`; if (r) out += `.padding(.trailing, ${r})`; }
  return out;
}

/// One modifier per line (SwiftUI house style): split a chain at top-level `.name(` after `)` or `}`, skipping strings.
function fmt(chain, ind) {
  const parts = [];
  let depth = 0, quote = false, start = 0;
  for (let i = 0; i < chain.length; i++) {
    const c = chain[i];
    if (quote) { if (c === '\\') i++; else if (c === '"') quote = false; continue; }
    if (c === '"') quote = true;
    else if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) depth--;
    else if (c === '.' && !depth && /[)}]/.test(chain[i - 1]) && /[a-z]/.test(chain[i + 1])) { parts.push(chain.slice(start, i)); start = i; }
  }
  parts.push(chain.slice(start));
  const modInd = parts[0].endsWith('}') ? ind : ind + '  ';
  return parts.join(`\n${modInd}`);
}

export function generate(input, { name, ignoreEdges = 'all', sourceName = 'ir', probes = true } = {}) {
  const { ir, root, nodes } = plan(input);
  const gaps = new Set();
  const { textView, decorationView } = paint(gaps);
  const local = [];  // SwiftUI-only limits, on top of stacks.mjs' unlowered
  // RN's root ignores the safe area, but a scroll view with automatic inset adjustment still insets its content where
  // it overlaps the safe area. Under .ignoresSafeArea() SwiftUI sees zero insets, so read them outside and re-apply.
  const ignored = { all: ['top', 'bottom'], bottom: ['bottom'], none: [] }[ignoreEdges];
  const win = root.n.frame;
  let needInsets = false;
  function scrollInsets(p) {
    if (!['automatic', 'always'].includes(p.n.contentInsetAdjustmentBehavior)) return '';
    const f = p.n.frame;
    // ponytail: without reference frames (jest IR) assume the scroll view touches both edges
    const edges = ignored.filter(e => !f || !win || (e === 'top' ? f[1] <= win[1] + 0.5 : f[1] + f[3] >= win[1] + win[3] - 0.5));
    if (edges.length) needInsets = true;
    return edges.map(e => `.contentMargins(.${e}, safe.safeAreaInsets.${e}, for: .scrollContent)`).join('');
  }

  const probe = p => (probes ? `.spectraFrame(${p.i})` : '');
  const id = p => (p.n.testID ? `.accessibilityIdentifier(${JSON.stringify(p.n.testID)})` : '');
  const opacity = s => (s.opacity !== undefined && s.opacity !== 1 ? `.opacity(${s.opacity})` : '');
  const background = s => {
    const d = decorationView(s);
    if (!d) return '';
    return `.background(${s.borderRadius >= 999 ? d.replace(/RoundedRectangle\(cornerRadius: \d+, style: \.continuous\)/g, 'Capsule()') : d})`;
  };

  /// Sizing inside the padding: explicit sizes become content sizes (Yoga's width includes padding), stretch -> .infinity.
  function frames(p) {
    let out = '';
    const a = alignment(p.content ?? { h: 'start', v: 'start' });
    const at = a === 'center' ? '' : `, alignment: .${a}`;
    const w = p.width !== undefined ? p.width - p.pad.l - p.pad.r : undefined;
    const h = p.height !== undefined ? p.height - p.pad.t - p.pad.b : undefined;
    const leafBox = p.kind === 'box' || p.kind === 'image';  // greedy views: unstretched axes get their padding-only size
    const fixed = [w !== undefined || (leafBox && !p.sx) ? `width: ${w ?? 0}` : '', h !== undefined || (leafBox && !p.sy) ? `height: ${h ?? 0}` : ''].filter(Boolean);
    if (fixed.length) out += `.frame(${fixed.join(', ')}${at})`;
    const grow = [p.sx && w === undefined ? 'maxWidth: .infinity' : '', p.sy && h === undefined ? 'maxHeight: .infinity' : ''].filter(Boolean);
    if (grow.length) out += `.frame(${grow.join(', ')}${at})`;
    return out;
  }

  function emit(p, ind) {
    const s = p.s;
    const pad = padding(p.pad);
    if (p.kind === 'pass') return fmt(emit(p.child, ind) + id(p) + probe(p) + padding(p.margin), ind);
    let body;
    if (p.kind === 'text') body = textView(p.n);
    else if (p.kind === 'box') body = 'Color.clear';
    else if (p.kind === 'image') { gaps.add(`image ${JSON.stringify(p.n.source ?? '')} (#${p.i}, placeholder)`); body = 'Color.gray.opacity(0.2)'; }
    else if (p.kind === 'scroll') {
      const inner = p.child ? emit(p.child, ind + '  ') + pad : '';
      body = `ScrollView${p.horizontal ? '(.horizontal)' : ''} {\n${ind}  ${inner}\n${ind}}`;
      return fmt(body + scrollInsets(p) + frames(p) + background(s) + opacity(s) + id(p) + probe(p) + padding(p.margin), ind);
    } else {
      const v = p.axis === 'v';
      const cross = v ? p.content.h : p.content.v;
      const alignArg = cross === 'center' ? '' : `alignment: .${v ? H[cross] : V[cross]}, `;
      const flow = p.children.filter(c => !c.absolute);
      for (const c of flow) {
        const ca = c.alignSelf === 'stretch' ? 'start' : { 'flex-start': 'start', center: 'center', 'flex-end': 'end' }[c.alignSelf];
        if (c.alignSelf !== p.align && ca !== cross) local.push(`#${c.i}: alignSelf ${c.alignSelf} in a ${p.align} stack`);
      }
      const between = p.spacers === 'between';
      const kids = flow.map(c => emit(c, ind + '  '));
      const lines = between ? kids.flatMap((k, j) => (j ? ['Spacer(minLength: 0)', k] : [k])) : kids;
      body = `${v ? 'VStack' : 'HStack'}(${alignArg}spacing: ${between ? p.gap / 2 : p.gap}) {\n${lines.map(l => `${ind}  ${l}`).join('\n')}\n${ind}}`;
      if (p.fix) body += `.fixedSize(horizontal: ${v}, vertical: ${!v})`;
    }
    return fmt(body + frames(p) + pad + background(s) + overlays(p, ind) + opacity(s) + id(p) + probe(p) + padding(p.margin), ind);
  }

  /// Absolute children: overlays on the parent's border box, aligned to the inset edges, offset by padding.
  function overlays(p, ind) {
    return (p.children ?? []).filter(c => c.absolute).map(c => {
      const a = c.absolute;
      const at = alignment({ h: a.left === undefined && a.right !== undefined ? 'end' : 'start', v: a.top === undefined && a.bottom !== undefined ? 'end' : 'start' });
      const ins = padding({ t: a.top ?? 0, l: a.left ?? 0, b: a.bottom ?? 0, r: a.right ?? 0 });
      return `.overlay(alignment: .${at}) {\n${ind}  ${fmt(emit(c, ind + '  ') + ins, ind + '  ')}\n${ind}}`;
    }).join('');
  }

  const view = emit(root, '    ');
  const cov = coverage(root);
  cov.unlowered.push(...local);
  cov.lowered = cov.nodes - new Set(cov.unlowered.map(u => u.split(/[ :]/)[0])).size;
  const swift = `// Generated by Spectra (backends/swiftui/lower.mjs) from ${sourceName} (${ir.source ?? 'rn'} screen ${ir.screen}). Do not edit.
// Native SwiftUI stacks: ${cov.lowered}/${cov.nodes} nodes lowered by rule, ${cov.pass} pass-through wrappers removed.
// Unlowered (needs a lowering rule or the agent):
${cov.unlowered.map(u => `//   - ${u}`).join('\n') || '//   none'}
// Gaps (not reproduced, reported instead of guessed):
${[...gaps].sort().map(g => `//   - ${g}`).join('\n') || '//   none'}
import SwiftUI

struct ${name}: View {
${probes ? `  /// (composition 0, every IR node frame in preorder), for the parity judge.
  var onFrames: ((Int, [CGRect]) -> Void)? = nil

` : ''}  var body: some View {
${needInsets ? `    GeometryReader { safe in  // insets read inside the safe area, applied to the scroll content below\n      ${view.replace(/\n/g, '\n  ')}${IGNORE[ignoreEdges] ? `\n      ${IGNORE[ignoreEdges]}` : ''}\n    }` : `    ${view}${IGNORE[ignoreEdges] ? `\n    ${IGNORE[ignoreEdges]}` : ''}`}${probes ? `\n    .spectraFrames(${nodes}, onFrames)` : ''}
  }
}
`;
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
