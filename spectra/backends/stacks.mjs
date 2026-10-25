// Yoga IR -> stack plan: the analysis both native-stack back-ends share (swiftui/lower.mjs, uikit/lower.mjs).
// Per node it decides what a stack layout must do to land where Yoga put it: axis and alignment, whether the parent
// stretches it (sx/sy), whether its size is fixed from outside (detX/detY), how justify is realised (spacers), and which
// wrappers are pure pass-throughs. Whatever a stack can't express goes in `unlowered` (per node), never guessed.
// Deterministic, no model. Preorder indices match the judge's (every IR node, collapsed or not).
import { normalize } from '../ir/ir.mjs';

const num = v => typeof v === 'number';
const SIDES = { t: ['Top', 'Vertical'], b: ['Bottom', 'Vertical'], l: ['Left', 'Horizontal', 'Start'], r: ['Right', 'Horizontal', 'End'] };
const MAP = { 'flex-start': 'start', start: 'start', center: 'center', 'flex-end': 'end', end: 'end', stretch: 'start', baseline: 'start' };

/** Yoga edge precedence (edge > start/end > axis > all) -> { t, l, b, r } in points. */
function edges(s, p, bad) {
  const out = { t: 0, l: 0, b: 0, r: 0 };
  for (const [k, [edge, axis, alias]] of Object.entries(SIDES)) {
    const v = s[`${p}${edge}`] ?? (alias && s[`${p}${alias}`]) ?? s[`${p}${axis}`] ?? s[p];
    if (v === undefined) continue;
    if (!num(v)) bad.push(`${p}${edge}: ${JSON.stringify(v)}`); else out[k] = v;
  }
  return out;
}

export const growOf = s => s.flexGrow ?? (num(s.flex) && s.flex > 0 ? s.flex : 0);
const hasPaint = s => (s.backgroundColor && s.backgroundColor !== 'transparent') || s.shadowColor || s.outlineWidth
  || Object.keys(s).some(k => /^border.*Width$/.test(k) && s[k]) || (s.opacity !== undefined && s.opacity !== 1);
const zero = e => !e.t && !e.l && !e.b && !e.r;

export function plan(input) {
  const ir = normalize(input);
  let counter = 0;

  function build(n, ctx) {
    const i = counter++;
    const s = n.style ?? {};
    const unlowered = [];
    const p = { i, n, s, sx: ctx.sx, sy: ctx.sy, unlowered,
      width: num(s.width) ? s.width : undefined, height: num(s.height) ? s.height : undefined,
      pad: edges(s, 'padding', unlowered), margin: edges(s, 'margin', unlowered) };
    p.detX = ctx.detX || p.width !== undefined;
    p.detY = ctx.detY || p.height !== undefined;

    if (s.flexWrap && s.flexWrap !== 'nowrap') unlowered.push(`flexWrap: ${s.flexWrap}`);
    if (s.aspectRatio !== undefined) unlowered.push('aspectRatio');
    if (s.display === 'none') unlowered.push('display: none');
    for (const k of ['width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight', 'flexBasis'])
      if (s[k] !== undefined && !(k === 'flexBasis' && (s[k] === '0%' || s[k] === 0 || s[k] === 'auto')) && !(num(s[k]) && (k === 'width' || k === 'height')))
        unlowered.push(`${k}: ${JSON.stringify(s[k])}`);
    if (Object.keys(s).some(k => /^border.*Width$/.test(k) && s[k])) unlowered.push('border widths');
    if (n.type === 'safe-area') unlowered.push('SafeAreaView (native safe area differs)');

    if (n.type === 'text') { p.kind = 'text'; p.content = { h: MAP[s.textAlign === 'right' ? 'end' : s.textAlign] ?? 'start', v: 'start' }; return p; }
    if (n.type === 'image') { p.kind = 'image'; if (p.width === undefined && !p.sx) unlowered.push('image without size'); return p; }
    if (n.type === 'scroll') {
      p.kind = 'scroll';
      p.horizontal = !!n.horizontal;
      const c = n.children?.[0];
      if (n.children?.length > 1) unlowered.push('scroll view with several children');
      // Content: stretched across the scroll axis' cross, free along it. The scroll view's padding insets it.
      if (c) p.child = build(c, p.horizontal ? { sx: false, sy: p.detY, detX: false, detY: p.detY }
        : { sx: p.detX, sy: false, detX: p.detX, detY: false });
      return p;
    }
    const kids = n.children ?? [];
    if (!kids.length) { p.kind = 'box'; return p; }

    const dir = s.flexDirection ?? 'column';
    if (dir.endsWith('reverse')) unlowered.push(`flexDirection: ${dir}`);
    p.axis = dir.startsWith('row') ? 'h' : 'v';
    p.align = s.alignItems ?? 'stretch';
    p.justify = s.justifyContent ?? 'flex-start';
    p.gap = (p.axis === 'v' ? s.rowGap : s.columnGap) ?? s.gap ?? 0;
    const v = p.axis === 'v';
    const detMain = v ? p.detY : p.detX, detCross = v ? p.detX : p.detY;
    p.fix = false;
    const growers = [];
    const flow = kids.filter(c => c.style?.position !== 'absolute');
    if (!flow.length) unlowered.push('only absolute children');
    p.children = kids.map(c => {
      const cs = c.style ?? {};
      if (cs.position === 'absolute') {
        // Out of flow: placed by insets from the parent's border box (back-ends: overlay / pinned subview).
        const ins = {};
        for (const [k, alias] of [['top'], ['bottom'], ['left', 'start'], ['right', 'end']]) {
          const v = cs[k] ?? (alias && cs[alias]);
          if (v === undefined) continue;
          if (!num(v)) unlowered.push(`#${counter} ${k}: ${JSON.stringify(v)}`); else ins[k] = v;
        }
        const sx = ins.left !== undefined && ins.right !== undefined && !num(cs.width);
        const sy = ins.top !== undefined && ins.bottom !== undefined && !num(cs.height);
        const child = build(c, { sx: sx && p.detX, sy: sy && p.detY, detX: num(cs.width) || (sx && p.detX), detY: num(cs.height) || (sy && p.detY) });
        child.absolute = ins;
        child.alignSelf = p.align;
        return child;
      }
      const ca = cs.alignSelf && cs.alignSelf !== 'auto' ? cs.alignSelf : p.align;
      const crossFixed = num(v ? cs.width : cs.height), mainFixed = num(v ? cs.height : cs.width);
      let stretchCross = false;
      if (ca === 'stretch' && !crossFixed) {
        if (detCross) stretchCross = true;
        // Stretch inside a hugging parent = as wide as the widest sibling; a lone child is that already.
        else if (flow.length > 1) { stretchCross = true; p.fix = true; }
      }
      const g = growOf(cs);
      const stretchMain = g > 0 && detMain && !mainFixed;  // grow does nothing when the main size is undefined
      if (stretchMain) growers.push({ g, basis0: cs.flexBasis === 0 || cs.flexBasis === '0%' || (num(cs.flex) && cs.flex > 0 && cs.flexBasis === undefined) });
      const crossDet = crossFixed || (stretchCross && detCross), mainDet = mainFixed || stretchMain;
      const child = build(c, v ? { sx: stretchCross, sy: stretchMain, detX: crossDet, detY: mainDet }
        : { sx: stretchMain, sy: stretchCross, detX: mainDet, detY: crossDet });
      child.alignSelf = ca;
      return child;
    });
    // Several growers lower only as an equal split: same ratio, zero basis, and nothing else in the flow.
    p.equal = growers.length > 1 && growers.length === flow.length && growers.every(x => x.basis0 && x.g === growers[0].g);
    if (growers.length > 1 && !p.equal) unlowered.push(`${growers.length} growing children (stacks don't split by flexGrow ratio)`);

    // Justify only matters when the main size is fixed from outside and nothing grows into the slack.
    p.spacers = 'none';
    if (detMain && !growers.length && flow.length) {
      p.spacers = { 'flex-start': 'end', start: 'end', center: 'both', 'flex-end': 'start', end: 'start',
        'space-between': flow.length > 1 ? 'between' : 'end' }[p.justify];
      if (!p.spacers) { unlowered.push(`justifyContent: ${p.justify}`); p.spacers = 'end'; }
    }
    const main = { end: 'start', start: 'end', both: 'center' }[p.spacers] ?? 'start';
    const cross = MAP[p.align] ?? 'start';
    p.content = v ? { h: cross, v: main } : { h: main, v: cross };

    // A wrapper with no paint, padding or size whose only child fills it is a pass-through: no native view.
    const c = p.children[0];
    if (kids.length === 1 && !c.absolute && n.type === 'view' && !hasPaint(s) && zero(p.pad) && p.width === undefined
      && p.height === undefined && !unlowered.length && (!p.sx || c.sx) && (!p.sy || c.sy) && !(n.testID && c.n.testID)) {
      p.kind = 'pass';
      p.child = c;
      return p;
    }
    p.kind = 'stack';
    return p;
  }

  const root = build(ir.roots[0], { sx: true, sy: true, detX: true, detY: true });
  if (ir.roots.length > 1) root.unlowered.push(`${ir.roots.length} roots (only the first is lowered)`);
  return { ir, root, nodes: counter };
}

/** Preorder walk over plan nodes (pass-throughs included). */
export function walk(p, f) {
  f(p);
  for (const c of p.children ?? (p.child ? [p.child] : [])) walk(c, f);
}

/** Lowering coverage: how many IR nodes became native stacks/leaves by rule, how many collapsed, what's left. */
export function coverage(root) {
  const r = { nodes: 0, pass: 0, unlowered: [] };
  walk(root, p => {
    r.nodes++;
    if (p.kind === 'pass') r.pass++;
    if (p.unlowered.length) r.unlowered.push(`#${p.i} ${p.n.testID ?? p.n.type}: ${p.unlowered.join(', ')}`);
  });
  r.lowered = r.nodes - r.unlowered.length;
  return r;
}
