// Web back-end: IR -> one self-contained HTML page whose CSS flexbox carries RN/Yoga's defaults (as react-native-web
// sets them), so a browser lays the screen out the way Yoga does. For cheap previews without an Xcode build, and as a
// second layout oracle beside FlexCore (measure.mjs judges it with verify/judge.mjs).
// Not drawn: images (grey placeholders), native chrome (status bar, nav bar), behaviour.
import { normalize } from '../../ir/ir.mjs';

// iPhone 17 Pro portrait. ponytail: one device; take insets per device when a fixture needs another.
export const SAFE_AREA = { top: 62, bottom: 34 };

const PX = new Set(['width', 'height', 'minWidth', 'minHeight', 'maxWidth', 'maxHeight', 'top', 'left', 'right', 'bottom',
  'margin', 'marginTop', 'marginBottom', 'marginLeft', 'marginRight', 'padding', 'paddingTop', 'paddingBottom',
  'paddingLeft', 'paddingRight', 'gap', 'rowGap', 'columnGap', 'borderRadius', 'fontSize', 'lineHeight', 'letterSpacing',
  'borderWidth', 'outlineWidth', 'outlineOffset', 'flexBasis']);
const PAIRS = { paddingVertical: ['paddingTop', 'paddingBottom'], paddingHorizontal: ['paddingLeft', 'paddingRight'],
  marginVertical: ['marginTop', 'marginBottom'], marginHorizontal: ['marginLeft', 'marginRight'] };
const kebab = k => k.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

// RN style -> inline CSS. `extra` adds to padding (safe-area insets).
export function css(style = {}, extra = {}) {
  const s = {};
  for (const [k, v] of Object.entries(style)) {
    if (PAIRS[k]) for (const p of PAIRS[k]) s[p] ??= v;
    else s[k] = v;
  }
  for (const [k, v] of Object.entries(extra)) s[k] = (s[k] ?? s.padding ?? 0) + v;
  if (s.flex !== undefined) { // RN: flex > 0 is grow f, shrink 1, basis 0; flex 0 is fixed
    const f = s.flex; delete s.flex;
    if (f > 0) Object.assign(s, { flexGrow: f, flexShrink: 1, flexBasis: 0 });
  }
  if (s.shadowColor !== undefined || s.shadowRadius !== undefined) {
    const o = s.shadowOffset ?? { width: 0, height: 0 }, a = s.shadowOpacity ?? 1;
    const color = String(s.shadowColor ?? 'rgba(0, 0, 0, 1)').replace(/rgba\(([^)]*),\s*[\d.]+\)/, `rgba($1, ${a})`);
    s.boxShadow = `${o.width}px ${o.height}px ${s.shadowRadius ?? 0}px ${color}`;
  }
  if (s.overflow === 'scroll') s.overflow = 'hidden'; // the scroll element itself scrolls (see node())
  const out = [];
  for (const [k, v] of Object.entries(s)) {
    if (typeof v === 'object' || k.startsWith('shadow')) continue;
    if (k === 'fontWeight') out.push(`font-weight:${v === 'bold' ? 700 : v === 'normal' ? 400 : v}`);
    else if (k === 'outlineWidth') out.push(`outline-width:${v}px;outline-style:solid`);
    else out.push(`${kebab(k)}:${typeof v === 'number' && PX.has(k) ? v + 'px' : v}`);
  }
  return out.join(';');
}

// render(ir, { safeArea, ignoreEdges, probe }) -> { html, nodes }. Nodes carry data-i in preorder, the judge's order.
// ignoreEdges: which safe-area edges the root ignores ('all' for RN, as `spectra gen` defaults; 'none' for Figma). A
// scroll view with automatic inset adjustment insets its content on those edges where it touches the window.
// probe: add a script that writes every node's box into <pre id="spectra-boxes"> (measure.mjs reads it).
export function render(input, { safeArea = SAFE_AREA, ignoreEdges, probe = false } = {}) {
  const ir = normalize(input);
  const ignored = { all: ['top', 'bottom'], bottom: ['bottom'], none: [] }[ignoreEdges ?? (ir.source === 'figma' ? 'none' : 'all')];
  const win = ir.roots[0]?.frame;
  const [w, h] = ir.container ?? win?.slice(2) ?? [402, 874];
  let i = 0;
  function node(n) {
    const id = i++, cls = n.type === 'text' ? 't' : n.type === 'scroll' ? 'v s' : 'v';
    const extra = {};
    if (n.type === 'safe-area') Object.assign(extra, { paddingTop: safeArea.top, paddingBottom: safeArea.bottom });
    if (n.type === 'scroll' && ['automatic', 'always'].includes(n.contentInsetAdjustmentBehavior)) {
      const f = n.frame; // without reference frames (jest IR) assume it touches both edges, as the SwiftUI back-end does
      for (const e of ignored) {
        if (!f || !win || (e === 'top' ? f[1] <= win[1] + 0.5 : f[1] + f[3] >= win[1] + win[3] - 0.5)) {
          extra[e === 'top' ? 'paddingTop' : 'paddingBottom'] = safeArea[e];
        }
      }
    }
    let style = css(n.horizontal ? { ...n.style, flexDirection: 'row' } : n.style, extra);
    if (n.numberOfLines) style += `${style && ';'}-webkit-line-clamp:${n.numberOfLines}`;
    const attrs = [`data-i="${id}"`, `class="${cls}"`, style && `style="${esc(style)}"`, n.testID && `data-testid="${esc(n.testID)}"`,
      n.numberOfLines && 'data-lines'].filter(Boolean).join(' ');
    if (n.type === 'image') return `<div ${attrs}></div>`;
    const kids = (n.children ?? []).map(node).join('');
    return `<div ${attrs}>${n.text !== undefined ? esc(n.text) : ''}${kids}</div>`;
  }
  const body = ir.roots.map(node).join('');
  const script = probe ? `<script>document.body.insertAdjacentHTML('beforeend','<pre id="spectra-boxes">'+JSON.stringify([...document.querySelectorAll('[data-i]')].map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]}))+'</pre>')</script>` : '';
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=${w}">
<title>${esc(ir.screen ?? 'Spectra screen')}</title>
<style>
html,body{margin:0;background:#fff}
#spectra-root{position:relative;width:${w}px;height:${h}px;display:flex;flex-direction:column;overflow:hidden}
.v,.t{box-sizing:border-box;display:flex;flex-direction:column;position:relative;flex-shrink:0;align-items:stretch;align-content:flex-start;min-width:0;min-height:0;border:0 solid #000;margin:0;padding:0}
.s{overflow:auto;scrollbar-width:none}
.t{display:block;font-family:-apple-system,system-ui,sans-serif;font-size:14px;color:#000;white-space:pre-wrap;overflow-wrap:break-word}
.t[data-lines]{display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden}
</style></head><body><div id="spectra-root">${body}</div>${script}</body></html>
`;
  return { html, nodes: i, ir };
}
