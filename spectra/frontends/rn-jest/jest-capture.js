// RN jest front-end: render an RN screen with react-test-renderer (no simulator, no Metro, any state via props/mocks) and
// emit Spectra IR (ir/README.md), the same as the runtime front-end minus frames. Host props already hold the final style objects
// (StyleSheet/inline), so nothing here knows about styling libraries.
// Use from a jest test running under the target app's jest preset:
//   const { captureScreen } = require('<this file>');
//   test('capture', () => captureScreen(<Detail navigation={nav} />, 'Detail', '/abs/out.json'));
const React = require('react');
const { create, act } = require('react-test-renderer');
const { writeFileSync } = require('fs');

// react-test-renderer names host components by their JS name; the runtime capture uses native view names.
const HOST = { View: 'view', Text: 'text', ScrollView: 'scroll', RCTScrollView: 'scroll', Image: 'image', RCTSafeAreaView: 'safe-area', SafeAreaView: 'safe-area' };
const flatten = s => (Array.isArray(s) ? Object.assign({}, ...s.map(flatten)) : s && typeof s === 'object' ? s : {});
const textOf = n => (typeof n === 'string' ? n : (n.children ?? []).map(textOf).join(''));

function host(n) {
  if (typeof n === 'string') return null;  // bare strings only occur inside Text
  const p = n.props ?? {};
  const node = { type: HOST[n.type] ?? 'view', style: flatten(p.style) };
  if (!HOST[n.type]) node.hostType = n.type;
  if (p.testID) node.testID = p.testID;
  if (p.numberOfLines) node.numberOfLines = p.numberOfLines;
  if (p.horizontal) node.horizontal = true;
  if (p.contentInsetAdjustmentBehavior) node.contentInsetAdjustmentBehavior = p.contentInsetAdjustmentBehavior;
  if (node.type === 'text') { node.text = textOf(n); return node; }
  if (p.source) node.source = p.source;
  node.children = (n.children ?? []).map(host).filter(Boolean);
  // ScrollView's mock renders the content container as a plain View child; the runtime tree calls it RCTScrollContentView.
  if (node.type === 'scroll') {
    node.children = [{ type: 'scroll-content', style: flatten(p.contentContainerStyle), children: node.children }];
  }
  return node;
}

function captureScreen(element, screen, out) {
  let r;
  act(() => { r = create(element); });
  const json = r.toJSON();
  const roots = (Array.isArray(json) ? json : json ? [json] : []).map(host).filter(Boolean);
  const cap = { ir: 1, screen, source: 'rn-jest', capturedAt: Date.now(), roots };
  writeFileSync(out, JSON.stringify(cap, null, 1));
  act(() => r.unmount());
  return cap;
}

module.exports = { captureScreen };
