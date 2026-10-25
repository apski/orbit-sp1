// SwiftUI paint: colour, text and decoration (background, outline, shadow) from RN style keys. Shared by the FlexLayout
// back-end (codegen.mjs) and the native-stacks back-end (lower.mjs). Unsupported values are added to `gaps`, never guessed.
const WEIGHTS = { 100: 'ultraLight', 200: 'thin', 300: 'light', 400: 'regular', normal: 'regular', 500: 'medium',
  600: 'semibold', 700: 'bold', bold: 'bold', 800: 'heavy', 900: 'black' };
export const SYSTEM_FONT = /^(System|-apple-system|\.SF|SF ?Pro|SFPro)/i;

/** RN colour -> [r, g, b, a] (0-255, alpha 0-1), or null when unparseable. */
export function parseColor(c) {
  const named = { white: [255, 255, 255, 1], black: [0, 0, 0, 1], transparent: [0, 0, 0, 0] };
  if (named[c]) return named[c];
  let m;
  if ((m = /^#([0-9a-f]{3,8})$/i.exec(c))) {
    let h = m[1]; if (h.length <= 4) h = [...h].map(x => x + x).join('');
    return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)).concat(h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1);
  }
  if ((m = /^rgba?\(([^)]+)\)$/.exec(c))) { const p = m[1].split(',').map(Number); return [p[0], p[1], p[2], p[3] ?? 1]; }
  return null;
}

export const weightName = w => WEIGHTS[w ?? 400] ?? 'regular';

export function paint(gaps) {
  function color(c) {
    if (c === undefined || c === null) return null;
    if (c === 'transparent') return 'Color.clear';
    const rgba = parseColor(c);
    if (!rgba) { gaps.add(`color ${JSON.stringify(c)}`); return null; }
    const f = v => +(v / 255).toFixed(4);
    return `Color(red: ${f(rgba[0])}, green: ${f(rgba[1])}, blue: ${f(rgba[2])}, opacity: ${+(+rgba[3]).toFixed(4)})`;
  }

  function textView(n) {
    const s = n.style;
    const size = s.fontSize ?? 14, weight = weightName(s.fontWeight);
    let v = `Text(${JSON.stringify(n.text ?? '')})`;
    if (s.fontFamily && !SYSTEM_FONT.test(s.fontFamily)) {
      gaps.add(`font "${s.fontFamily}" must be bundled in the app (falls back to the system font if missing)`);
      // A PostScript name (Inter-SemiBold) already names the weight; a family name (Inter) takes it from fontWeight.
      v += `.font(.custom(${JSON.stringify(s.fontFamily)}, fixedSize: ${size})${s.fontFamily.includes('-') ? '' : `.weight(.${weight})`})`;
    } else {
      v += `.font(.system(size: ${size}, weight: .${weight}))`;
    }
    if (s.fontStyle === 'italic') v += '.italic()';
    v += `.foregroundStyle(${color(s.color ?? 'black') ?? 'Color.black'})`;
    if (s.lineHeight) v += `.lineHeight(.exact(points: ${s.lineHeight}))`;
    if (s.letterSpacing) v += `.kerning(${s.letterSpacing})`;
    if (s.textDecorationLine?.includes('underline')) v += '.underline()';
    if (s.textDecorationLine?.includes('line-through')) v += '.strikethrough()';
    if (s.textAlign === 'center') v += '.multilineTextAlignment(.center)';
    if (s.textAlign === 'right') v += '.multilineTextAlignment(.trailing)';
    if (n.numberOfLines) v += `.lineLimit(${n.numberOfLines})`;
    return v;
  }

  /// Background, outline (drawn outside layout, like Figma strokes and RN outline) and shadow, at the node's frame.
  function decorationView(s) {
    const fill = color(s.backgroundColor);
    const stroke = s.outlineWidth ? color(s.outlineColor ?? 'black') : null;
    const hasShadow = s.shadowColor && (s.shadowOpacity ?? 1) > 0;
    if ((!fill || fill === 'Color.clear') && !stroke && !hasShadow) return null;
    if (!stroke && !hasShadow && !s.borderRadius) return fill;
    const shape = `RoundedRectangle(cornerRadius: ${s.borderRadius ?? 0}, style: .continuous)`;
    let v = `${shape}.fill(${fill ?? 'Color.clear'})`;
    if (stroke) {
      const inset = -(s.outlineOffset ?? 0) - s.outlineWidth / 2;  // stroke centred on the outline's mid-line
      v += `.overlay(${shape}.inset(by: ${+inset.toFixed(3)}).stroke(${stroke}, lineWidth: ${s.outlineWidth}))`;
    }
    if (hasShadow) {
      const c = color(s.shadowColor) ?? 'Color.black';
      const o = s.shadowOffset ?? { width: 0, height: 0 };
      v += `.shadow(color: ${c}.opacity(${s.shadowOpacity ?? 1}), radius: ${s.shadowRadius ?? 0}, x: ${o.width}, y: ${o.height})`;
    }
    return v;
  }

  return { color, textView, decorationView };
}
