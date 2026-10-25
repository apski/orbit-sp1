// Builds checkout.figma.json: a Figma REST-shaped frame whose boxes are worked out BY HAND from Figma's auto-layout rules
// (not by running Yoga), so judging the generated SwiftUI against them independently tests the Figma lowering.
// Every text box has a fixed or fill width and an explicit line height, so no box depends on font metrics.
import { writeFileSync } from 'node:fs';
const O = { x: 1000, y: 2000 };  // non-zero page position: lowering must make frames root-relative
const bb = (x, y, width, height) => ({ x: O.x + x, y: O.y + y, width, height });
const hex = (h, a = 1) => ({ r: parseInt(h.slice(1, 3), 16) / 255, g: parseInt(h.slice(3, 5), 16) / 255, b: parseInt(h.slice(5, 7), 16) / 255, a });
const fill = (h, a) => [{ type: 'SOLID', visible: true, opacity: 1, blendMode: 'NORMAL', color: hex(h, a) }];
let id = 100;
const text = (name, chars, box, size, weight, lh, color, sizing, align = 'LEFT') => ({
  id: `1:${id++}`, name, type: 'TEXT', visible: true, characters: chars, absoluteBoundingBox: bb(...box),
  layoutSizingHorizontal: sizing[0], layoutSizingVertical: sizing[1], fills: fill(color), characterStyleOverrides: [],
  style: { fontFamily: 'SF Pro Text', fontPostScriptName: weight >= 600 ? 'SFProText-Semibold' : 'SFProText-Regular', fontWeight: weight,
    fontSize: size, textAlignHorizontal: align, textAlignVertical: 'TOP', letterSpacing: 0, lineHeightPx: lh, lineHeightUnit: 'PIXELS',
    textAutoResize: sizing[0] === 'HUG' ? 'WIDTH_AND_HEIGHT' : 'HEIGHT' } });
const frame = (name, box, props, children = []) => ({ id: `1:${id++}`, name, type: 'FRAME', visible: true, absoluteBoundingBox: bb(...box),
  fills: [], strokes: [], effects: [], clipsContent: false, children, ...props });
const row = (name, y, label, value, weight) => frame(name, [40, y, 313, 22],
  { layoutMode: 'HORIZONTAL', primaryAxisAlignItems: 'SPACE_BETWEEN', counterAxisAlignItems: 'CENTER', itemSpacing: 10,
    layoutSizingHorizontal: 'FILL', layoutSizingVertical: 'HUG' },
  [text('Label', label, [40, y, 120, 22], 17, weight, 22, '#3C3C43', ['FIXED', 'HUG']),
   text('Value', value, [253, y, 100, 22], 17, weight === 400 ? 600 : weight, 22, '#000000', ['FIXED', 'HUG'], 'RIGHT')]);
const button = (name, x, style, label, bg, fg, comp) => ({ ...frame(name, [x, 191, 166.5, 50],
  { layoutMode: 'HORIZONTAL', primaryAxisAlignItems: 'CENTER', counterAxisAlignItems: 'CENTER', paddingTop: 14, paddingBottom: 14,
    paddingLeft: 16, paddingRight: 16, layoutSizingHorizontal: 'FILL', layoutSizingVertical: 'HUG', cornerRadius: 12, fills: fill(bg) },
  [text('Label', label, [x + 16, 205, 134.5, 22], 17, 600, 22, fg, ['FILL', 'HUG'], 'CENTER')]),
  type: 'INSTANCE', componentId: comp,
  componentProperties: { Style: { type: 'VARIANT', value: style }, 'Label#12:0': { type: 'TEXT', value: label } } });

const document = frame('Checkout', [0, 0, 393, 852], {
  layoutMode: 'VERTICAL', primaryAxisAlignItems: 'MIN', counterAxisAlignItems: 'MIN', itemSpacing: 16,
  paddingTop: 24, paddingRight: 24, paddingBottom: 24, paddingLeft: 24, fills: fill('#F7F7F8') }, [
  text('Title', 'Order summary', [24, 24, 345, 34], 28, 700, 34, '#000000', ['FILL', 'HUG']),
  frame('Card', [24, 74, 345, 101], {
    layoutMode: 'VERTICAL', itemSpacing: 12, paddingTop: 16, paddingRight: 16, paddingBottom: 16, paddingLeft: 16,
    layoutSizingHorizontal: 'FILL', layoutSizingVertical: 'HUG', cornerRadius: 16, fills: fill('#FFFFFF'),
    strokes: fill('#E5E5EA'), strokeWeight: 1, strokeAlign: 'INSIDE',
    effects: [{ type: 'DROP_SHADOW', visible: true, color: { r: 0, g: 0, b: 0, a: 0.08 }, offset: { x: 0, y: 2 }, radius: 8, spread: 0 }] }, [
    row('Row', 90, 'Subtotal', '£42.00', 400),
    { id: `1:${id++}`, name: 'Divider', type: 'RECTANGLE', visible: true, absoluteBoundingBox: bb(40, 124, 313, 1),
      layoutSizingHorizontal: 'FILL', layoutSizingVertical: 'FIXED', fills: fill('#E5E5EA'), strokes: [], effects: [] },
    row('Row2', 137, 'Total', '£45.50', 700),
  ]),
  frame('Actions', [24, 191, 345, 50], { layoutMode: 'HORIZONTAL', itemSpacing: 12, layoutSizingHorizontal: 'FILL', layoutSizingVertical: 'HUG' }, [
    button('Button', 24, 'Secondary', 'Back', '#E5E5EA', '#007AFF', '10:1'),
    button('Button', 202.5, 'Primary', 'Pay £45.50', '#007AFF', '#FFFFFF', '10:2'),
  ]),
  frame('Footer', [24, 257, 345, 18], { layoutMode: 'HORIZONTAL', counterAxisAlignItems: 'CENTER', itemSpacing: 8,
    layoutSizingHorizontal: 'FILL', layoutSizingVertical: 'HUG' }, [
    { id: `1:${id++}`, name: 'Dot', type: 'ELLIPSE', visible: true, absoluteBoundingBox: bb(24, 262, 8, 8),
      layoutSizingHorizontal: 'FIXED', layoutSizingVertical: 'FIXED', fills: fill('#34C759'), strokes: [], effects: [] },
    { id: `1:${id++}`, name: 'Lock', type: 'VECTOR', visible: true, absoluteBoundingBox: bb(40, 260, 12, 12),
      layoutSizingHorizontal: 'FIXED', layoutSizingVertical: 'FIXED', fills: fill('#8E8E93'), strokes: [], effects: [] },
    text('Note', 'Secure checkout', [60, 257, 309, 18], 13, 400, 18, '#8E8E93', ['FILL', 'HUG']),
  ]),
  frame('Badge', [309, 28, 60, 24], { layoutPositioning: 'ABSOLUTE', constraints: { horizontal: 'RIGHT', vertical: 'TOP' },
    layoutMode: 'HORIZONTAL', primaryAxisAlignItems: 'CENTER', counterAxisAlignItems: 'CENTER',
    paddingTop: 4, paddingBottom: 4, paddingLeft: 8, paddingRight: 8, layoutSizingHorizontal: 'FIXED', layoutSizingVertical: 'FIXED',
    cornerRadius: 12, fills: fill('#FF3B30') }, [
    text('Label', 'NEW', [317, 32, 44, 16], 12, 700, 16, '#FFFFFF', ['FILL', 'HUG'], 'CENTER'),
  ]),
]);
document.id = '1:1';
writeFileSync(new URL('checkout.figma.json', import.meta.url), JSON.stringify({
  fileKey: 'FIXTUREkey123', nodeId: '1:1', name: 'Spectra fixtures', version: 'fixture', document,
  components: { '10:1': { key: 'k-secondary', name: 'Style=Secondary', componentSetId: '9:0' },
                '10:2': { key: 'k-primary', name: 'Style=Primary', componentSetId: '9:0' } },
  componentSets: { '9:0': { key: 'k-button', name: 'Button' } } }, null, 1));
