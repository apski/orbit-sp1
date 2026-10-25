// Figma front-end: lowering facts on the hand-computed fixture, URL parsing, cache round trip. `node --test tests/*.test.mjs`
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { lower } from '../frontends/figma/lower.mjs';
import { parseUrl, readCache, writeCache, cachePath } from '../frontends/figma/api.mjs';
import { validate } from '../ir/ir.mjs';

const doc = readCache(new URL('../fixtures/figma/checkout.figma.json', import.meta.url).pathname);
const { ir, gaps } = lower(doc);
const find = (name, n = ir.roots[0], hits = []) => { if (n.name === name) hits.push(n); (n.children ?? []).forEach(c => find(name, c, hits)); return hits; };

test('IR is valid and root-relative', () => {
  assert.deepEqual(validate(ir), []);
  assert.deepEqual(ir.roots[0].frame, [0, 0, 393, 852]);
  assert.deepEqual(ir.container, [393, 852]);
  assert.deepEqual(find('Badge')[0].frame, [309, 28, 60, 24]);
});

test('root fills its container; auto layout becomes flex', () => {
  const r = ir.roots[0].style;
  assert.equal(r.flexGrow, 1); assert.equal(r.alignSelf, 'stretch');
  assert.equal(r.flexDirection, 'column'); assert.equal(r.rowGap, 16); assert.equal(r.paddingLeft, 24);
  assert.equal(r.alignItems, 'flex-start');  // Figma never stretches unless the child is FILL
  assert.equal(r.backgroundColor, 'rgba(247, 247, 248, 1)');
});

test('sizing: FILL on the cross axis stretches, on the main axis grows from zero, FIXED sets the size', () => {
  const title = find('Title')[0].style;
  assert.equal(title.alignSelf, 'stretch'); assert.equal(title.width, undefined); assert.equal(title.height, undefined);
  const [back, pay] = find('Button');
  for (const b of [back, pay]) { assert.equal(b.style.flexGrow, 1); assert.equal(b.style.flexBasis, 0); assert.equal(b.style.width, undefined); }
  assert.equal(find('Label')[0].style.width, 120);
  assert.equal(find('Divider')[0].style.height, 1);
});

test('SPACE_BETWEEN ignores itemSpacing, like Figma', () => {
  const rowStyle = find('Row')[0].style;
  assert.equal(rowStyle.justifyContent, 'space-between'); assert.equal(rowStyle.columnGap, undefined);
});

test('absolute child pinned by its constraints', () => {
  const s = find('Badge')[0].style;
  assert.deepEqual([s.position, s.right, s.top, s.width, s.height, s.left], ['absolute', 24, 28, 60, 24, undefined]);
});

test('paint: inside stroke as an outline, shadow at half the blur, ellipse radius', () => {
  const card = find('Card')[0].style;
  assert.deepEqual([card.outlineWidth, card.outlineOffset, card.borderRadius], [1, -1, 16]);
  assert.deepEqual([card.shadowRadius, card.shadowOpacity, card.shadowOffset.height], [4, 0.08, 2]);
  assert.equal(find('Dot')[0].style.borderRadius, 4);
  assert.equal(find('Lock')[0].type, 'image');
});

test('text style and component instances', () => {
  const title = find('Title')[0];
  assert.deepEqual([title.type, title.text, title.style.fontSize, title.style.fontWeight, title.style.lineHeight],
    ['text', 'Order summary', 28, '700', 34]);
  assert.equal(title.style.fontFamily, undefined);  // SF Pro -> system font
  assert.deepEqual(find('Button')[1].component, { name: 'Button', key: 'k-primary', props: { Style: 'Primary', Label: 'Pay £45.50' } });
  assert.equal(find('Value')[0].style.textAlign, 'right');
});

test('the fixture lowers without gaps (the vector asset is a codegen gap)', () => {
  assert.deepEqual(gaps, []);
});

test('URL parsing: design, branch and node-id forms', () => {
  assert.deepEqual(parseUrl('https://www.figma.com/design/AbC123/My-File?node-id=12-345&t=x'), { fileKey: 'AbC123', nodeId: '12:345' });
  assert.deepEqual(parseUrl('https://www.figma.com/design/AbC123/branch/BrX9/My-File?node-id=1-2'), { fileKey: 'BrX9', nodeId: '1:2' });
  assert.equal(parseUrl('https://www.figma.com/file/AbC123/x').nodeId, null);
  assert.throws(() => parseUrl('https://example.com/x'));
});

test('cache round trip (zstd when Node has it)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'spectra-'));
  const p = cachePath(dir, 'K', '1:2');
  writeCache(p, doc);
  assert.deepEqual(readCache(p), doc);
});
