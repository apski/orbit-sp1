// Oracle for FlexLayout: seeded random flex trees laid out by yoga-layout, configured like React Native
// (errata All: YogaLayoutableShadowNode.cpp defaultErrata; no web defaults; no pixel rounding).
// Usage: node gen.mjs [--n 2000] [--seed 1] [--out ../cases/cases.json]
import Yoga, { Align, Direction, Edge, Errata, FlexDirection, Gutter, Justify, MeasureMode, PositionType } from 'yoga-layout';
import { mkdirSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname } from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const ORACLE = arg('oracle', 'native'); // native = RN's vendored Yoga (native/oracle.cpp), npm = yoga-layout
const N = +arg('n', 2000), SEED = +arg('seed', 1), OUT = arg('out', new URL('../cases/cases.json', import.meta.url).pathname);

function rng(seed) { // mulberry32
  return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// Text stand-in, mirrored exactly in Swift (Measure.text): monospace 7pt advance, 17pt lines, wraps by character.
export function measureText(n, width, widthMode) {
  if (widthMode === MeasureMode.Undefined || Number.isNaN(width)) return { width: n * 7, height: 17 };
  const cpl = Math.min(n, Math.max(1, Math.floor(width / 7)));
  const lines = Math.ceil(n / cpl);
  return { width: widthMode === MeasureMode.Exactly ? width : cpl * 7, height: lines * 17 };
}

const JUSTIFY = ['flex-start', 'center', 'flex-end', 'space-between', 'space-around', 'space-evenly'];
const ALIGN = ['stretch', 'flex-start', 'center', 'flex-end'];

function genTree(r, depth) {
  const pick = a => a[Math.floor(r() * a.length)];
  const int = (lo, hi) => lo + Math.floor(r() * (hi - lo + 1));
  const maybe = (p, f) => (r() < p ? f() : undefined);
  const style = {};
  // item props (meaningful for any non-root node)
  style.flexGrow = maybe(0.3, () => pick([0, 0.5, 1, 1, 2, 3]));
  style.flexShrink = maybe(0.3, () => pick([0, 0.5, 1, 1, 2]));
  style.flex = maybe(0.15, () => pick([1, 1, 2]));
  style.flexBasis = maybe(0.15, () => int(0, 160));
  style.alignSelf = maybe(0.2, () => pick(ALIGN));
  style.width = maybe(0.2, () => int(10, 260));
  style.height = maybe(0.2, () => int(10, 120));
  style.minWidth = maybe(0.1, () => int(0, 120));
  style.maxWidth = maybe(0.1, () => int(20, 300));
  style.minHeight = maybe(0.1, () => int(0, 80));
  style.maxHeight = maybe(0.1, () => int(10, 160));
  for (const e of ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft']) style[e] = maybe(0.3, () => int(1, 16));
  if (depth > 0) {
    for (const e of ['marginTop', 'marginRight', 'marginBottom', 'marginLeft']) style[e] = maybe(0.2, () => int(-4, 16));
    style.position = maybe(0.1, () => 'absolute');
    for (const e of ['top', 'right', 'bottom', 'left']) style[e] = maybe(style.position ? 0.4 : 0.05, () => int(-8, 40));
  }
  const node = { style };
  const leaf = depth > 0 && (depth >= 3 || r() < 0.35);
  if (leaf) {
    const k = r();
    if (k < 0.5) node.measure = { kind: 'text', n: int(1, 60) };
    else if (k < 0.75) node.measure = { kind: 'fixed', w: int(8, 64), h: int(8, 64) };
    // else: empty View
  } else {
    style.flexDirection = pick(['column', 'row']);
    style.justifyContent = maybe(0.6, () => pick(JUSTIFY));
    style.alignItems = maybe(0.6, () => pick(ALIGN));
    style.rowGap = maybe(0.25, () => int(1, 12));
    style.columnGap = maybe(0.25, () => int(1, 12));
    node.children = Array.from({ length: int(1, 4) }, () => genTree(r, depth + 1));
  }
  for (const k of Object.keys(style)) if (style[k] === undefined) delete style[k];
  return node;
}

const E = { 'flex-start': Justify.FlexStart, center: Justify.Center, 'flex-end': Justify.FlexEnd,
  'space-between': Justify.SpaceBetween, 'space-around': Justify.SpaceAround, 'space-evenly': Justify.SpaceEvenly };
const A = { stretch: Align.Stretch, 'flex-start': Align.FlexStart, center: Align.Center, 'flex-end': Align.FlexEnd };

function build(config, spec) {
  const y = Yoga.Node.create(config), s = spec.style;
  if (s.flexDirection) y.setFlexDirection(s.flexDirection === 'row' ? FlexDirection.Row : FlexDirection.Column);
  if (s.justifyContent) y.setJustifyContent(E[s.justifyContent]);
  if (s.alignItems) y.setAlignItems(A[s.alignItems]);
  if (s.alignSelf) y.setAlignSelf(A[s.alignSelf]);
  if (s.flexGrow !== undefined) y.setFlexGrow(s.flexGrow);
  if (s.flexShrink !== undefined) y.setFlexShrink(s.flexShrink);
  if (s.flex !== undefined) y.setFlex(s.flex);
  if (s.flexBasis !== undefined) y.setFlexBasis(s.flexBasis);
  for (const [k, f] of [['width', 'setWidth'], ['height', 'setHeight'], ['minWidth', 'setMinWidth'], ['maxWidth', 'setMaxWidth'],
    ['minHeight', 'setMinHeight'], ['maxHeight', 'setMaxHeight']]) if (s[k] !== undefined) y[f](s[k]);
  for (const [k, e] of [['paddingTop', Edge.Top], ['paddingRight', Edge.Right], ['paddingBottom', Edge.Bottom], ['paddingLeft', Edge.Left]])
    if (s[k] !== undefined) y.setPadding(e, s[k]);
  for (const [k, e] of [['marginTop', Edge.Top], ['marginRight', Edge.Right], ['marginBottom', Edge.Bottom], ['marginLeft', Edge.Left]])
    if (s[k] !== undefined) y.setMargin(e, s[k]);
  if (s.position === 'absolute') y.setPositionType(PositionType.Absolute);
  for (const [k, e] of [['top', Edge.Top], ['right', Edge.Right], ['bottom', Edge.Bottom], ['left', Edge.Left]])
    if (s[k] !== undefined) y.setPosition(e, s[k]);
  if (s.rowGap !== undefined) y.setGap(Gutter.Row, s.rowGap);
  if (s.columnGap !== undefined) y.setGap(Gutter.Column, s.columnGap);
  const m = spec.measure;
  if (m?.kind === 'text') y.setMeasureFunc((w, wm) => measureText(m.n, w, wm));
  if (m?.kind === 'fixed') y.setMeasureFunc(() => ({ width: m.w, height: m.h }));
  (spec.children ?? []).forEach((c, i) => y.insertChild(build(config, c), i));
  return y;
}

function frames(y, out = []) {
  const l = y.getComputedLayout();
  out.push([l.left, l.top, l.width, l.height]);
  for (let i = 0; i < y.getChildCount(); i++) frames(y.getChild(i), out);
  return out;
}

// Preorder text form read by native/oracle.cpp.
function serialise(spec, out) {
  const m = spec.measure;
  const kv = Object.entries(spec.style).flatMap(([k, v]) => [k, v]);
  out.push([(spec.children ?? []).length, m ? (m.kind === 'text' ? `text ${m.n}` : `fixed ${m.w} ${m.h}`) : 'none', ...kv, ';'].join(' '));
  (spec.children ?? []).forEach(c => serialise(c, out));
  return out;
}
function nativeFrames(cases) {
  const bin = arg('bin', new URL('native/oracle', import.meta.url).pathname);
  const input = cases.map(c => { const lines = serialise(c.root, []);
    return `case ${c.id} ${c.width} ${c.height ?? 'nan'} ${lines.length}\n${lines.join('\n')}`; }).join('\n') + '\n';
  const r = spawnSync(bin, { input, maxBuffer: 1 << 30 });
  if (r.status !== 0) throw new Error(`oracle failed: ${r.stderr}`);
  const byId = new Map(); let cur;
  for (const line of r.stdout.toString().split('\n')) {
    if (line.startsWith('case ')) byId.set(+line.slice(5), cur = []);
    else if (line) cur.push(line.split(' ').map(v => (v === 'nan' || v === '-nan' ? null : +v)));
  }
  return byId;
}

const config = Yoga.Config.create();
config.setErrata(Errata.All);
config.setPointScaleFactor(0);
const r = rng(SEED), cases = [];
for (let id = 0; id < N; id++) {
  const root = genTree(r, 0);
  root.style.flexDirection ??= 'column';
  const width = 200 + Math.floor(r() * 231), height = r() < 0.5 ? null : 200 + Math.floor(r() * 700);
  const c = { id, width, height, root };
  if (ORACLE === 'npm') {
    const y = build(config, root);
    y.calculateLayout(width, height ?? undefined, Direction.LTR);
    c.frames = frames(y);
    y.freeRecursive();
  }
  cases.push(c);
}
if (ORACLE === 'native') { const f = nativeFrames(cases); for (const c of cases) c.frames = f.get(c.id); }
mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify({ oracle: ORACLE === 'npm' ? 'yoga-layout@3.2.1' : 'react-native@0.76.5 vendored yoga', errata: 'All', pointScaleFactor: 0, seed: SEED, cases }));
console.log(`wrote ${cases.length} cases -> ${OUT}`);
