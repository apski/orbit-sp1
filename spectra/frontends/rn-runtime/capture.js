// RN runtime front-end: evaluated inside a running RN app (Fabric) via the JS debugger. Walks React's host fibers
// under the named screen component and returns Spectra IR (ir/README.md): resolved host styles, text, Yoga frames.
// No per-styling-library parsing: NativeWind/StyleSheet/inline styles are already resolved on host props.
// Usage: evaluate this file's text, then `__orbitCapture("OnboardingWelcomeScreen")` (returns a JSON string).
globalThis.__orbitCapture = function (screenName) {
  const hook = globalThis.__REACT_DEVTOOLS_GLOBAL_HOOK__;
  const roots = [];
  for (const id of hook.renderers.keys()) for (const r of hook.getFiberRoots(id)) roots.push(r.current);
  const nameOf = f => f.type && (f.type.displayName || f.type.name);
  const find = f => { for (; f; f = f.sibling) { if (nameOf(f) === screenName) return f; const c = find(f.child); if (c) return c; } };
  let screen; for (const r of roots) if ((screen = find(r))) break;
  if (!screen) return JSON.stringify({ error: `no fiber named ${screenName}` });

  const IR = { RCTView: 'view', RCTText: 'text', RCTParagraph: 'text', RCTImageView: 'image', RCTScrollView: 'scroll',
    RCTScrollContentView: 'scroll-content', RCTSafeAreaView: 'safe-area' };
  const flatten = s => Array.isArray(s) ? Object.assign({}, ...s.map(flatten)) : (s && typeof s === 'object' ? s : {});
  const frameOf = f => {
    let out = null;
    const sn = f.stateNode && f.stateNode.node;
    if (sn) globalThis.nativeFabricUIManager.measureInWindow(sn, (x, y, w, h) => { out = [x, y, w, h]; });
    return out;
  };
  const textOf = f => { let s = ''; for (let c = f.child; c; c = c.sibling) s += c.tag === 6 ? c.memoizedProps : textOf(c); return s; };
  const host = f => {
    const p = f.memoizedProps || {};
    const node = { type: IR[f.type] ?? 'view', style: flatten(p.style), frame: frameOf(f) };
    if (!IR[f.type]) node.hostType = f.type;
    if (p.testID) node.testID = p.testID;
    if (p.numberOfLines) node.numberOfLines = p.numberOfLines;
    if (p.horizontal) node.horizontal = true;
    if (p.contentInsetAdjustmentBehavior) node.contentInsetAdjustmentBehavior = p.contentInsetAdjustmentBehavior;
    if (node.type === 'text') { node.text = textOf(f); return node; }  // text is a leaf
    if (p.source) node.source = p.source;
    node.children = [];
    collect(f.child, node.children);
    return node;
  };
  const collect = (f, out) => { for (; f; f = f.sibling) { if (f.tag === 5) out.push(host(f)); else collect(f.child, out); } };
  const top = []; collect(screen.child, top);
  return JSON.stringify({ ir: 1, screen: screenName, source: 'rn-runtime', capturedAt: Date.now(), roots: top });
};
