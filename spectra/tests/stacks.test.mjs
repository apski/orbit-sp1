// Native-stack lowering (backends/stacks.mjs + swiftui/uikit lower.mjs) on the three fixtures. `node --test tests/`
// Frames are checked on the simulator by `spectra verify --backend swiftui|uikit`; this pins the rule decisions.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { plan, coverage, walk } from '../backends/stacks.mjs';
import { generate as swiftui } from '../backends/swiftui/lower.mjs';
import { generate as uikit } from '../backends/uikit/lower.mjs';
import { readCache } from '../frontends/figma/api.mjs';
import { lower } from '../frontends/figma/lower.mjs';

const json = f => JSON.parse(readFileSync(new URL(`../fixtures/${f}`, import.meta.url), 'utf8'));
const fixtures = {
  home: json('HomeScreen.json'),
  detail: json('NavCoreDetail.runtime.json'),
  checkout: lower(readCache(new URL('../fixtures/figma/checkout.figma.json', import.meta.url).pathname)).ir,
};

test('every fixture node lowers by rule, on both back-ends', () => {
  for (const [k, ir] of Object.entries(fixtures)) {
    for (const gen of [swiftui, uikit]) {
      const r = gen(ir, { name: 'X' });
      assert.deepEqual(r.coverage.unlowered, [], `${k}: ${r.coverage.unlowered}`);
      assert.equal(r.map.length, r.nodes);
    }
  }
});

test('pressable wrappers collapse to their label; ids survive', () => {
  const { root } = plan(fixtures.detail);
  assert.equal(coverage(root).pass, 6);
  const s = swiftui(fixtures.detail, { name: 'X', probes: false }).swift;
  assert.match(s, /Text\("Open transaction"\)[\s\S]*?\.padding\(8\)\s*\.accessibilityIdentifier\("detail.openTransaction"\)/);
  assert.doesNotMatch(s, /VStack\(alignment: \.leading, spacing: 0\) \{\s*Text\("Open/);  // no wrapper stacks left
});

test('stretch, grow, absolute and equal split decisions', () => {
  const { root } = plan(fixtures.checkout);
  const byName = {};
  walk(root, p => { byName[p.n.name] ??= p; });
  assert.equal(byName.Title.sx, true);                       // alignSelf stretch in a flex-start column
  assert.deepEqual(byName.Badge.absolute, { top: 28, right: 24 });
  assert.equal(byName.Actions.equal, true);                  // two flex:1 buttons
  const u = uikit(fixtures.checkout, { name: 'X' }).swift;
  assert.match(u, /distribution = \.fillEqually/);
  assert.match(u, /insetsLayoutMarginsFromSafeArea = false/);
  const s = swiftui(fixtures.checkout, { name: 'X' }).swift;
  assert.match(s, /\.overlay\(alignment: \.topTrailing\)/);
});

test('scroll view keeps RN inset behaviour', () => {
  const s = swiftui(fixtures.home, { name: 'X' }).swift;
  assert.match(s, /\.contentMargins\(\.top, safe\.safeAreaInsets\.top, for: \.scrollContent\)/);
  assert.doesNotMatch(s, /contentMargins\(\.bottom/);       // the scroll view stops above the footer
  assert.match(uikit(fixtures.home, { name: 'X' }).swift, /alwaysBounceVertical = true/);
});

test('output is deterministic', () => {
  for (const ir of Object.values(fixtures)) assert.equal(swiftui(ir, { name: 'X' }).swift, swiftui(ir, { name: 'X' }).swift);
});
