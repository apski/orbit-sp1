// Web back-end (backends/web): IR -> HTML laid out by the browser's flexbox, judged against the fixtures' reference
// frames with verify/judge.mjs, the same 1pt rule the native back-ends meet. Needs Chrome (skips without it).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { render } from '../backends/web/render.mjs';
import { chrome, judgeWeb } from '../backends/web/measure.mjs';
import { readCache } from '../frontends/figma/api.mjs';
import { lower } from '../frontends/figma/lower.mjs';

const json = f => JSON.parse(readFileSync(new URL(`../fixtures/${f}`, import.meta.url), 'utf8'));
const fixtures = {
  home: json('HomeScreen.json'), // scroll view with automatic content inset: content starts below the 62pt top inset
  detail: json('NavCoreDetail.runtime.json'),
  checkout: lower(readCache(new URL('../fixtures/figma/checkout.figma.json', import.meta.url).pathname)).ir,
};

for (const [k, ir] of Object.entries(fixtures)) {
  test(`${k}: every node within 1pt in the browser`, { skip: !chrome() && 'no Chrome' }, () => {
    const r = judgeWeb(ir);
    const off = r.rows.filter(x => !x.ok).map(x => `#${x.i} ${x.label} off ${x.d.toFixed(2)}pt`);
    assert.deepEqual(off, [], `${k}: ${r.within}/${r.total}`);
  });
}

test('render: RN defaults, scroll without scrollbar, escaped text, no script unless measuring', () => {
  const { html, nodes } = render({ ir: 1, screen: 'X', roots: [{ type: 'scroll', style: { overflow: 'scroll' },
    children: [{ type: 'scroll-content', children: [{ type: 'text', text: '<b>&', style: { fontWeight: 'bold', marginVertical: 4 } }] }] }] });
  assert.equal(nodes, 3);
  assert.match(html, /flex-direction:column/);
  assert.match(html, /scrollbar-width:none/);
  assert.match(html, /&lt;b&gt;&amp;/);
  assert.match(html, /font-weight:700;margin-top:4px;margin-bottom:4px/);
  assert.doesNotMatch(html, /overflow:scroll/);
  assert.doesNotMatch(html, /<script/);
  assert.match(render({ ir: 1, roots: [] }, { probe: true }).html, /<script/);
});
