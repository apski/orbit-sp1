// Spectra UI IR: what every front-end (RN runtime, RN jest, Figma) emits and every back-end reads.
// Layout uses Yoga's vocabulary (RN style keys), because the native runtime is a Yoga port: front-ends lower into it,
// back-ends only execute it. Spec: ir/README.md.
export const IR_VERSION = 1;
export const TYPES = ['view', 'text', 'image', 'scroll', 'scroll-content', 'safe-area'];

// Native-view names from RN captures made before the IR existed.
const LEGACY = { RCTView: 'view', RCTText: 'text', RCTParagraph: 'text', RCTImageView: 'image', RCTScrollView: 'scroll',
  RCTScrollContentView: 'scroll-content', RCTSafeAreaView: 'safe-area' };

/** Normalise a capture (any version) to IR v1; unknown host types are kept as `view` with `hostType` recorded. */
export function normalize(doc) {
  const node = n => {
    const type = LEGACY[n.type] ?? (TYPES.includes(n.type) ? n.type : 'view');
    const out = { ...n, type, style: n.style ?? {} };
    if (type !== n.type && !LEGACY[n.type]) out.hostType = n.type;
    if (n.children) out.children = n.children.map(node);
    return out;
  };
  return { ir: IR_VERSION, ...doc, roots: (doc.roots ?? []).map(node) };
}

/** Structural checks only; returns a list of problems (empty = valid). */
export function validate(doc) {
  const errs = [];
  if (doc.ir !== IR_VERSION) errs.push(`ir version ${doc.ir} (want ${IR_VERSION})`);
  if (!Array.isArray(doc.roots)) errs.push('roots must be an array');
  const walk = (n, path) => {
    if (!TYPES.includes(n.type)) errs.push(`${path}: type ${n.type}`);
    if (n.type === 'text' && typeof n.text !== 'string') errs.push(`${path}: text node without text`);
    if (n.type === 'text' && n.children?.length) errs.push(`${path}: text node with children`);
    if (n.frame && (n.frame.length !== 4 || n.frame.some(v => typeof v !== 'number'))) errs.push(`${path}: bad frame`);
    (n.children ?? []).forEach((c, i) => walk(c, `${path}/${i}`));
  };
  (doc.roots ?? []).forEach((r, i) => walk(r, `roots/${i}`));
  return errs;
}
