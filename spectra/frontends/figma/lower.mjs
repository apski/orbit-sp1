// Figma front-end: a pulled Figma node (REST JSON) -> Spectra IR. Auto layout is lowered into Yoga's vocabulary, so
// the Swift runtime lays it out natively; each node keeps Figma's absoluteBoundingBox (root-relative) as its reference
// frame for the judge. Anything that doesn't lower exactly is returned in `gaps`, never approximated silently.
//
//   Figma (auto-layout parent)          IR / Yoga
//   layoutMode HORIZONTAL|VERTICAL      flexDirection row|column
//   itemSpacing, padding*               rowGap/columnGap (negative: margins), padding*
//   primaryAxisAlignItems               justifyContent (SPACE_BETWEEN ignores itemSpacing, as in Figma)
//   counterAxisAlignItems               alignItems
//   child sizing FIXED|HUG|FILL         width/height set | unset | main: flexGrow 1 + flexBasis 0, cross: alignSelf stretch
//   layoutPositioning ABSOLUTE, or a    position absolute + insets from constraints (LEFT/RIGHT/LEFT_RIGHT, TOP/...)
//   non-auto-layout parent (frame, group)
//   strokes (outside layout)            outlineColor/Width/Offset (INSIDE -w, CENTER -w/2, OUTSIDE 0)
//   DROP_SHADOW                         shadowColor/Opacity/Offset, shadowRadius = blur / 2 (CALayer radius)
import { readCache } from './api.mjs';

const JUSTIFY = { MIN: 'flex-start', CENTER: 'center', MAX: 'flex-end', SPACE_BETWEEN: 'space-between' };
const ALIGN = { MIN: 'flex-start', CENTER: 'center', MAX: 'flex-end', BASELINE: 'flex-start' };
const SYSTEM_FAMILIES = /^(SF Pro|SF Compact|SF UI|\.SF|System)/i;
const SHAPES = new Set(['RECTANGLE', 'ELLIPSE']);
const PICTURES = new Set(['VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'POLYGON', 'LINE', 'REGULAR_POLYGON']);
const r2 = v => Math.round(v * 1000) / 1000;

function rgba(c, opacity = 1) {
  const a = r2((c.a ?? 1) * opacity);
  return `rgba(${Math.round(c.r * 255)}, ${Math.round(c.g * 255)}, ${Math.round(c.b * 255)}, ${a})`;
}

/** First visible solid paint (Figma paints are bottom-to-top; RN has one background, so more than one is a gap). */
function solid(paints, gaps, where, what) {
  const vis = (paints ?? []).filter(p => p.visible !== false);
  if (vis.length > 1) gaps.add(`${what}: ${vis.length} stacked paints, top one used (${where})`);
  const p = vis[vis.length - 1];
  if (!p) return null;
  if (p.type !== 'SOLID') { gaps.add(`${what}: ${p.type} paint not reproduced (${where})`); return null; }
  return rgba(p.color, p.opacity ?? 1);
}

/** sizing per axis: explicit layoutSizing* when present, else derived from the older fields. */
function sizing(n, axis, parentRow) {
  const explicit = axis === 'h' ? n.layoutSizingHorizontal : n.layoutSizingVertical;
  if (explicit) return explicit;
  const main = (axis === 'h') === parentRow;
  if (main && n.layoutGrow === 1) return 'FILL';
  if (!main && n.layoutAlign === 'STRETCH') return 'FILL';
  if (n.type === 'TEXT') {
    const r = n.style?.textAutoResize;
    if (r === 'WIDTH_AND_HEIGHT') return 'HUG';
    if (r === 'HEIGHT') return axis === 'h' ? 'FIXED' : 'HUG';
    return 'FIXED';
  }
  const own = n.layoutMode && n.layoutMode !== 'NONE' ? (n.layoutMode === 'HORIZONTAL') === (axis === 'h') : null;
  if (own === true && n.primaryAxisSizingMode === 'AUTO') return 'HUG';
  if (own === false && n.counterAxisSizingMode === 'AUTO') return 'HUG';
  return 'FIXED';
}

export function lower(doc, { root: rootMode = 'fill' } = {}) {
  const gaps = new Set();
  const top = doc.document;
  const box = n => n.absoluteBoundingBox ?? { x: 0, y: 0, width: 0, height: 0 };
  const origin = box(top);

  function node(n, parent, isRoot) {
    if (n.visible === false) return null;
    const where = `${n.type} "${n.name}"`;
    const b = box(n), pb = parent ? box(parent) : b;
    const style = {};
    const out = { type: 'view', name: n.name, figmaId: n.id, style,
      frame: [r2(b.x - origin.x), r2(b.y - origin.y), r2(b.width), r2(b.height)] };
    if (n.rotation && Math.abs(n.rotation) > 0.01) gaps.add(`rotation not reproduced (${where})`);

    // --- how this node sits in its parent
    const parentAuto = parent && parent.layoutMode && parent.layoutMode !== 'NONE' && parent.layoutMode !== 'GRID';
    if (parent?.layoutMode === 'GRID') gaps.add(`grid auto layout not lowered (${where})`);
    if (isRoot) {
      if (rootMode === 'fill') { style.flexGrow = 1; style.alignSelf = 'stretch'; }
      else { style.width = r2(b.width); style.height = r2(b.height); }
    } else if (parentAuto && n.layoutPositioning !== 'ABSOLUTE') {
      const row = parent.layoutMode === 'HORIZONTAL';
      for (const axis of ['h', 'v']) {
        const dim = axis === 'h' ? 'width' : 'height', main = (axis === 'h') === row;
        const s = sizing(n, axis, row);
        if (s === 'FIXED') style[dim] = r2(axis === 'h' ? b.width : b.height);
        else if (s === 'FILL') { if (main) { style.flexGrow = 1; style.flexBasis = 0; } else style.alignSelf = 'stretch'; }
      }
    } else {
      // Absolute (frame/group parent, or an absolute child): insets from constraints, measured from the parent's box.
      style.position = 'absolute';
      const c = n.constraints ?? { horizontal: 'LEFT', vertical: 'TOP' };
      const l = r2(b.x - pb.x), t = r2(b.y - pb.y), r = r2(pb.x + pb.width - b.x - b.width), btm = r2(pb.y + pb.height - b.y - b.height);
      const H = { LEFT: { left: l, width: b.width }, RIGHT: { right: r, width: b.width }, LEFT_RIGHT: { left: l, right: r },
        CENTER: { left: l, width: b.width }, SCALE: { left: l, width: b.width } };
      const V = { TOP: { top: t, height: b.height }, BOTTOM: { bottom: btm, height: b.height }, TOP_BOTTOM: { top: t, bottom: btm },
        CENTER: { top: t, height: b.height }, SCALE: { top: t, height: b.height } };
      Object.assign(style, H[c.horizontal] ?? H.LEFT, V[c.vertical] ?? V.TOP);
      for (const k of ['width', 'height']) if (style[k] !== undefined) style[k] = r2(style[k]);
      if (['CENTER', 'SCALE'].includes(c.horizontal) || ['CENTER', 'SCALE'].includes(c.vertical))
        gaps.add(`constraint ${c.horizontal}/${c.vertical} pinned to its design position (${where})`);
    }
    for (const k of ['minWidth', 'maxWidth', 'minHeight', 'maxHeight']) if (typeof n[k] === 'number') style[k] = n[k];

    // --- paint
    if (typeof n.opacity === 'number' && n.opacity < 1) style.opacity = r2(n.opacity);
    if (n.type !== 'TEXT') {
      const bg = solid(n.fills, gaps, where, 'fill');
      if (bg) style.backgroundColor = bg;
      if ((n.fills ?? []).some(p => p.visible !== false && p.type === 'IMAGE')) out.type = 'image';
    }
    if (n.type === 'ELLIPSE') style.borderRadius = r2(Math.min(b.width, b.height) / 2);
    else if (typeof n.cornerRadius === 'number') style.borderRadius = n.cornerRadius;
    if (n.rectangleCornerRadii && new Set(n.rectangleCornerRadii).size > 1) gaps.add(`mixed corner radii, ${n.rectangleCornerRadii} (${where})`);
    const stroke = n.type !== 'TEXT' && solid(n.strokes, gaps, where, 'stroke');
    if (stroke && n.strokeWeight) {
      style.outlineColor = stroke; style.outlineWidth = n.strokeWeight;
      style.outlineOffset = { INSIDE: -n.strokeWeight, CENTER: -n.strokeWeight / 2, OUTSIDE: 0 }[n.strokeAlign ?? 'INSIDE'];
      if (n.individualStrokeWeights) gaps.add(`per-side stroke weights (${where})`);
      if (n.strokesIncludedInLayout) gaps.add(`strokes included in layout (${where})`);
    }
    for (const e of (n.effects ?? []).filter(e => e.visible !== false)) {
      if (e.type === 'DROP_SHADOW' && !style.shadowColor) {
        style.shadowColor = rgba(e.color, 1).replace(/, [\d.]+\)$/, ', 1)');
        style.shadowOpacity = r2(e.color.a ?? 1);
        style.shadowOffset = { width: e.offset?.x ?? 0, height: e.offset?.y ?? 0 };
        style.shadowRadius = r2((e.radius ?? 0) / 2);
        if (e.spread) gaps.add(`shadow spread ${e.spread} (${where})`);
      } else gaps.add(`effect ${e.type} not reproduced (${where})`);
    }
    if (n.clipsContent && n.children?.length) style.overflow = 'hidden';

    // --- content
    if (n.type === 'TEXT') {
      out.type = 'text';
      const s = n.style ?? {};
      out.text = s.textCase === 'UPPER' ? n.characters.toUpperCase() : s.textCase === 'LOWER' ? n.characters.toLowerCase() : n.characters;
      const color = solid(n.fills, gaps, where, 'text fill');
      if (color) style.color = color;
      style.fontSize = s.fontSize;
      style.fontWeight = String(s.fontWeight ?? 400);
      if (s.fontFamily && !SYSTEM_FAMILIES.test(s.fontFamily)) style.fontFamily = s.fontPostScriptName ?? s.fontFamily;
      if (s.italic) style.fontStyle = 'italic';
      if (s.lineHeightUnit && s.lineHeightUnit !== 'INTRINSIC_%' && s.lineHeightPx) style.lineHeight = r2(s.lineHeightPx);
      if (s.letterSpacing) style.letterSpacing = r2(s.letterSpacing);
      const ta = { LEFT: 'left', CENTER: 'center', RIGHT: 'right', JUSTIFIED: 'justify' }[s.textAlignHorizontal];
      if (ta && ta !== 'left') style.textAlign = ta;
      if (ta === 'justify') gaps.add(`justified text (${where})`);
      if (s.textAlignVertical && s.textAlignVertical !== 'TOP' && sizing(n, 'v', parent?.layoutMode === 'HORIZONTAL') === 'FIXED')
        gaps.add(`vertical text alignment ${s.textAlignVertical} in a fixed-height box (${where})`);
      if (s.textDecoration === 'UNDERLINE') style.textDecorationLine = 'underline';
      if (s.textDecoration === 'STRIKETHROUGH') style.textDecorationLine = 'line-through';
      if (s.maxLines) out.numberOfLines = s.maxLines;
      else if (s.textAutoResize === 'TRUNCATE' || s.textTruncation === 'ENDING') out.numberOfLines = 1;
      if (n.characterStyleOverrides?.some(x => x !== 0)) gaps.add(`mixed text styles, base style used (${where})`);
      return out;
    }
    if (PICTURES.has(n.type)) {  // vector art: an exported asset, not layout
      out.type = 'image'; out.source = { figma: n.id };
      delete style.backgroundColor; delete style.outlineColor; delete style.outlineWidth; delete style.outlineOffset;
      return out;
    }
    if (n.type === 'INSTANCE' || n.type === 'COMPONENT') {
      const meta = doc.components?.[n.componentId ?? n.id] ?? {};
      const set = meta.componentSetId ? doc.componentSets?.[meta.componentSetId] : null;
      out.component = { name: set?.name ?? meta.name ?? n.name, key: meta.key,
        props: Object.fromEntries(Object.entries(n.componentProperties ?? {}).map(([k, v]) => [k.replace(/#[^#]*$/, ''), v.value])) };
    }

    // --- this node as a container
    const auto = n.layoutMode === 'HORIZONTAL' || n.layoutMode === 'VERTICAL';
    if (auto) {
      const row = n.layoutMode === 'HORIZONTAL';
      style.flexDirection = row ? 'row' : 'column';
      for (const e of ['Top', 'Right', 'Bottom', 'Left']) if (n[`padding${e}`]) style[`padding${e}`] = n[`padding${e}`];
      const j = n.primaryAxisAlignItems ?? 'MIN';
      if (j !== 'MIN') style.justifyContent = JUSTIFY[j];
      style.alignItems = ALIGN[n.counterAxisAlignItems ?? 'MIN'];  // Figma never stretches by default
      if (n.counterAxisAlignItems === 'BASELINE') gaps.add(`baseline alignment approximated as start (${where})`);
      if (n.layoutWrap === 'WRAP') {
        style.flexWrap = 'wrap';
        if (n.counterAxisSpacing) style[row ? 'rowGap' : 'columnGap'] = n.counterAxisSpacing;
      }
      if (n.itemReverseZIndex) gaps.add(`reversed z-order (${where})`);
    } else if (n.layoutMode === 'GRID') gaps.add(`grid auto layout not lowered (${where})`);
    const kids = (n.children ?? []).map(c => node(c, n, false)).filter(Boolean);
    if (auto && n.primaryAxisAlignItems !== 'SPACE_BETWEEN' && n.itemSpacing) {
      const row = n.layoutMode === 'HORIZONTAL';
      if (n.itemSpacing > 0) style[row ? 'columnGap' : 'rowGap'] = n.itemSpacing;
      else kids.filter(k => k.style.position !== 'absolute').slice(1).forEach(k => { k.style[row ? 'marginLeft' : 'marginTop'] = n.itemSpacing; });
    }
    if (kids.length) out.children = kids;
    if (SHAPES.has(n.type) && !kids.length) out.type = out.type === 'image' ? 'image' : 'view';
    return out;
  }

  const ir = { ir: 1, screen: top.name, source: 'figma', figma: { fileKey: doc.fileKey, nodeId: doc.nodeId, version: doc.version },
    container: [r2(origin.width), r2(origin.height)], roots: [node(top, null, true)].filter(Boolean) };
  return { ir, gaps: [...gaps].sort() };
}

export const lowerFile = (rawPath, opts) => lower(readCache(rawPath), opts);
