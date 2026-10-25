#!/usr/bin/env node
// navscan: static React Navigation tree scanner + per-push cost prediction from a NavLab model.
//
//   node navscan.mjs <repo> [--adapter <repo>/navlab.adapter.json] [--model <model.json>] [--learn] [--json out] [--top 15]
//
// Finds navigators (JSX API, static config API, Expo Router file tree), their screens and nesting, then scores
// every stack (where pushes happen) with the model: navigators on the path, tab/drawer routes on the path,
// assumed stack length. Anything it can't resolve is listed under "unresolved" with the adapter key that fixes it;
// --learn writes auto-detected facts (custom factory wrappers, aliases) into the adapter. See AGENTS.md §navscan.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const { parse } = createRequire(import.meta.url)('@babel/parser');

// React Navigation factories -> navigator kind. "tabs" = state holds every route (tabs, drawer, top tabs).
const FACTORIES = {
  createNativeStackNavigator: 'stack', createStackNavigator: 'stack', createSharedElementStackNavigator: 'stack',
  createBottomTabNavigator: 'tabs', createMaterialBottomTabNavigator: 'tabs', createNativeBottomTabNavigator: 'tabs',
  createMaterialTopTabNavigator: 'tabs', createDrawerNavigator: 'tabs',
};
const DRAWER = new Set(['createDrawerNavigator']);
const EXTS = ['.tsx', '.ts', '.jsx', '.js'];
const SKIP_DIRS = new Set(['node_modules', 'ios', 'android', '.git', 'build', 'dist', 'coverage', '__tests__', '__mocks__',
  '.expo', 'web-build', 'storybook', '.storybook', 'e2e', 'vendor', 'Pods']);
const TEST_FILE = /\.(test|spec|stories|story|mock|e2e)\.[jt]sx?$|\.test\.enzyme\.[jt]sx?$/;

// ---------- args, adapter ----------
const argv = process.argv.slice(2);
const opt = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const REPO = path.resolve(argv.find((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--')) || '.');
const ADAPTER_PATH = path.resolve(opt('--adapter', path.join(REPO, 'navlab.adapter.json')));
const adapter = fs.existsSync(ADAPTER_PATH) ? JSON.parse(fs.readFileSync(ADAPTER_PATH, 'utf8')) : {};
const MODEL_PATH = path.resolve(opt('--model', adapter.model ? path.resolve(REPO, adapter.model)
  : path.join(HERE, '..', 'baseline', 'sim-iphone17pro-ios26.4', 'model.json')));
const STACK_LEN = Number(opt('--stack', adapter.assumeStackLength ?? 5));
const UNKNOWN_LIST = Number(adapter.unknownListSize ?? 10);
const factories = { ...FACTORIES, ...(adapter.learned?.factories || {}), ...(adapter.factories || {}) };
const unresolved = [];
const learned = { factories: {}, aliases: {} };
const note = (kind, file, line, detail, fix) => unresolved.push({ kind, at: `${rel(file)}:${line}`, detail, fix });
const rel = (f) => path.relative(REPO, f);

// ---------- module resolution (relative, tsconfig paths/baseUrl, babel module-resolver, adapter aliases) ----------
// JSON with comments and trailing commas (tsconfig). String-aware: "app/*" is not a comment opener.
function readJsonLoose(f) {
  let src;
  try { src = fs.readFileSync(f, 'utf8'); } catch { return null; }
  let out = '', i = 0, str = false;
  while (i < src.length) {
    const c = src[i], n = src[i + 1];
    if (str) { out += c; if (c === '\\') { out += n; i += 2; continue; } if (c === '"') str = false; i++; continue; }
    if (c === '"') { str = true; out += c; i++; continue; }
    if (c === '/' && n === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (c === '/' && n === '*') { i = src.indexOf('*/', i + 2); i = i < 0 ? src.length : i + 2; continue; }
    out += c; i++;
  }
  try { return JSON.parse(out.replace(/,(\s*[}\]])/g, '$1')); } catch { return null; }
}
function detectAliases() {
  const out = {}, bases = [];
  const ts = readJsonLoose(path.join(REPO, 'tsconfig.json')) || readJsonLoose(path.join(REPO, 'jsconfig.json'));
  const co = ts?.compilerOptions || {};
  if (co.baseUrl) bases.push(path.resolve(REPO, co.baseUrl));
  for (const [k, v] of Object.entries(co.paths || {})) {
    const from = k.replace(/\*$/, ''), to = String(v[0] || '').replace(/\*$/, '');
    if (from) out[from] = path.relative(REPO, path.resolve(REPO, co.baseUrl || '.', to)) + (to.endsWith('/') ? '/' : '');
  }
  for (const f of ['babel.config.js', '.babelrc', '.babelrc.js', 'babel.config.json']) {
    const p = path.join(REPO, f);
    if (!fs.existsSync(p)) continue;
    const src = fs.readFileSync(p, 'utf8');
    const block = src.match(/alias\s*:\s*\{([^}]*)\}/);
    if (block) for (const m of block[1].matchAll(/['"]?([@~\w\-/.]+)['"]?\s*:\s*['"]([^'"]+)['"]/g)) out[m[1].endsWith('/') ? m[1] : m[1] + '/'] = m[2].replace(/^\.\//, '') + '/';
    const root = src.match(/root\s*:\s*\[([^\]]*)\]/);
    if (root) for (const m of root[1].matchAll(/['"]([^'"]+)['"]/g)) bases.push(path.resolve(REPO, m[1]));
  }
  return { aliases: out, bases };
}
const detected = detectAliases();
const ALIASES = { ...detected.aliases, ...(adapter.learned?.aliases || {}), ...(adapter.aliases || {}) };
const BASES = [...new Set([...detected.bases, ...(adapter.baseUrl ? [path.resolve(REPO, adapter.baseUrl)] : [])])];
Object.assign(learned.aliases, detected.aliases);

function tryFile(base) {
  for (const cand of [...(EXTS.includes(path.extname(base)) ? [base] : []), ...EXTS.flatMap((e) => [base + e, base.replace(/(\.[jt]sx?)?$/, '.ios' + e)]), ...EXTS.map((e) => path.join(base, 'index' + e))])
    if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return cand;
  return null;
}
function resolveModule(from, src) {
  if (src.startsWith('.')) return tryFile(path.resolve(path.dirname(from), src));
  for (const [a, to] of Object.entries(ALIASES)) if (src === a.replace(/\/$/, '') || src.startsWith(a)) {
    const hit = tryFile(path.resolve(REPO, to, src.slice(a.length)));
    if (hit) return hit;
  }
  for (const b of BASES) { const hit = tryFile(path.join(b, src)); if (hit) return hit; }
  return null; // package import
}

// ---------- parse + index ----------
function listFiles() {
  const roots = (adapter.roots || ['.']).map((r) => path.resolve(REPO, r));
  const ignore = (adapter.ignore || []).map((g) => new RegExp(g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*\*/g, '§').replace(/\*/g, '[^/]*').replace(/§/g, '.*')));
  const out = [];
  const walkDir = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (ignore.some((r) => r.test(rel(p)))) continue;
      if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name) && !e.name.startsWith('.')) walkDir(p); }
      else if (EXTS.includes(path.extname(e.name)) && !TEST_FILE.test(e.name) && !e.name.endsWith('.d.ts')) out.push(p);
    }
  };
  roots.forEach((r) => fs.existsSync(r) && walkDir(r));
  return out;
}

function walk(node, fn, parent) {
  if (!node || typeof node.type !== 'string') return;
  if (fn(node, parent) === false) return;
  for (const k in node) {
    if (k === 'loc' || k === 'extra' || k.endsWith('Comments')) continue;
    const v = node[k];
    if (Array.isArray(v)) { for (const c of v) if (c && typeof c.type === 'string') walk(c, fn, node); }
    else if (v && typeof v.type === 'string') walk(v, fn, node);
  }
}
const isComp = (n) => /^[A-Z]/.test(n || '');
const calleeName = (c) => c?.type === 'Identifier' ? c.name : c?.type === 'MemberExpression' && !c.computed ? c.property.name : null;
const jsxName = (n) => n?.type === 'JSXIdentifier' ? n.name : n?.type === 'JSXMemberExpression' ? `${jsxName(n.object)}.${n.property.name}` : null;
const unwrapExpr = (e) => (e && ['TSAsExpression', 'TSNonNullExpression', 'TypeCastExpression', 'ParenthesizedExpression', 'TSSatisfiesExpression'].includes(e.type) ? unwrapExpr(e.expression) : e);

const FILES = new Map(); // abs -> info
function indexFile(f) {
  const code = fs.readFileSync(f, 'utf8');
  const ext = path.extname(f);
  let ast;
  try {
    ast = parse(code, { sourceType: 'module', errorRecovery: true, allowReturnOutsideFunction: true,
      plugins: [ext === '.ts' ? 'typescript' : ext === '.tsx' ? ['typescript', { dts: false }] : 'flow', ...(ext === '.ts' ? [] : ['jsx']),
        'decorators-legacy', 'classProperties', 'exportDefaultFrom'] });
  } catch (e) { note('parse-error', f, 0, e.message.split('\n')[0], 'add the file to adapter.ignore if it is not app code'); return; }
  const info = { file: f, imports: new Map(), named: new Map(), reexports: [], defaultExport: null, decls: new Map() };
  const declare = (name, node, line) => node && info.decls.set(name, { node, line });
  for (const st of ast.program.body) {
    if (st.type === 'ImportDeclaration') {
      for (const s of st.specifiers) info.imports.set(s.local.name, { src: st.source.value, name: s.type === 'ImportDefaultSpecifier' ? 'default' : s.type === 'ImportNamespaceSpecifier' ? '*' : s.imported.name ?? s.imported.value });
      continue;
    }
    let d = st;
    if (st.type === 'ExportNamedDeclaration') {
      if (st.source) { for (const s of st.specifiers) info.reexports.push({ src: st.source.value, local: s.local?.name ?? 'default', exported: s.exported.name }); continue; }
      for (const s of st.specifiers || []) info.named.set(s.exported.name ?? s.exported.value, s.local.name);
      d = st.declaration;
      if (!d) continue;
      if (d.id) info.named.set(d.id.name, d.id.name);
      if (d.declarations) for (const v of d.declarations) if (v.id.type === 'Identifier') info.named.set(v.id.name, v.id.name);
    }
    if (st.type === 'ExportAllDeclaration') { info.reexports.push({ src: st.source.value, local: '*', exported: '*' }); continue; }
    if (st.type === 'ExportDefaultDeclaration') {
      const e = unwrapExpr(st.declaration);
      if (e.type === 'Identifier') info.defaultExport = e.name;
      else if ((e.type === 'FunctionDeclaration' || e.type === 'ClassDeclaration') && e.id) { declare(e.id.name, e, e.loc.start.line); info.defaultExport = e.id.name; }
      else { declare('__default', e, e.loc.start.line); info.defaultExport = '__default'; }
      continue;
    }
    if (d.type === 'FunctionDeclaration' && d.id) declare(d.id.name, d, d.loc.start.line);
    if (d.type === 'ClassDeclaration' && d.id) declare(d.id.name, d, d.loc.start.line);
    if (d.type === 'VariableDeclaration') for (const v of d.declarations) if (v.id.type === 'Identifier') declare(v.id.name, v.init && unwrapExpr(v.init), v.loc.start.line);
  }
  FILES.set(f, info);
}

// ---------- binding resolution ----------
// A binding is {file, name}. resolve() follows imports/re-exports/aliases/HOCs to the declaration.
function lookup(file, name, seen = new Set()) {
  const key = file + '#' + name;
  if (seen.has(key) || seen.size > 30) return null;
  seen.add(key);
  const info = FILES.get(file);
  if (!info) return null;
  if (name === 'default' && info.defaultExport) return lookup(file, info.defaultExport, seen); // else: `export default from './X'` re-export below
  const local = info.named.get(name) && info.named.get(name) !== name ? info.named.get(name) : name;
  if (info.decls.has(local)) return { file, name: local, ...info.decls.get(local) };
  const imp = info.imports.get(local);
  if (imp) {
    const target = resolveModule(file, imp.src);
    if (!target) return { file, name: local, external: imp.src, imported: imp.name };
    if (!FILES.has(target)) indexFile(target);
    return lookup(target, imp.name, seen);
  }
  for (const r of info.reexports) {
    if (r.exported !== name && r.exported !== '*') continue;
    const target = resolveModule(file, r.src);
    if (!target) continue;
    if (!FILES.has(target)) indexFile(target);
    const hit = lookup(target, r.exported === '*' ? name : r.local, seen);
    if (hit) return hit;
  }
  return null;
}

// What a declaration is: navigator instance, component, or wrapper around one.
function classify(b, depth = 0) {
  if (!b || depth > 6) return null;
  if (b.external) {
    if (factories[b.imported]) return null;
    return { type: 'external', src: b.external, name: b.imported };
  }
  const n = b.node;
  if (!n) return null;
  if (n.type === 'CallExpression') {
    const cn = calleeName(n.callee);
    const viaImport = n.callee.type === 'Identifier' ? lookup(b.file, n.callee.name) : null;
    const fname = viaImport?.external ? viaImport.imported : cn;
    if (factories[fname] || (viaImport && !viaImport.external && learnedFactory(viaImport))) {
      const kind = factories[fname] || learnedFactory(viaImport);
      return { type: 'nav', key: b.file + '#' + b.name, kind, factory: fname, drawer: DRAWER.has(fname), file: b.file, line: b.line, staticCfg: n.arguments[0]?.type === 'ObjectExpression' ? n.arguments[0] : null };
    }
    if (cn === 'createStaticNavigation') return null;
    // HOC: observer(X), memo(X), withFoo(X), connect(...)(X), forwardRef(fn)
    for (const a of [...n.arguments].reverse()) {
      const inner = unwrapExpr(a);
      if (inner.type === 'Identifier') { const r = classify(lookup(b.file, inner.name), depth + 1); if (r) return r; }
      if (inner.type === 'ArrowFunctionExpression' || inner.type === 'FunctionExpression') return { type: 'component', file: b.file, name: b.name, node: inner, line: b.line };
      if (inner.type === 'CallExpression') { const r = classify({ ...b, node: inner }, depth + 1); if (r) return r; } // withA(withB(observer(X)))
    }
    if (n.callee.type === 'CallExpression') return classify({ ...b, node: n.callee }, depth + 1);
    return null;
  }
  if (n.type === 'Identifier') return classify(lookup(b.file, n.name), depth + 1);
  if (['ArrowFunctionExpression', 'FunctionExpression', 'FunctionDeclaration', 'ClassDeclaration', 'ClassExpression'].includes(n.type))
    return { type: 'component', file: b.file, name: b.name, node: n, line: b.line };
  return null;
}

// Custom factory wrapper: a function whose body returns a known factory call. Learned and written to the adapter.
function learnedFactory(b) {
  const n = b?.node;
  if (!n || !['ArrowFunctionExpression', 'FunctionExpression', 'FunctionDeclaration'].includes(n.type)) return null;
  let kind = null;
  const returned = (e) => { e = unwrapExpr(e); if (e?.type === 'CallExpression' && factories[calleeName(e.callee)]) kind = factories[calleeName(e.callee)]; };
  if (n.body.type !== 'BlockStatement') returned(n.body);
  else for (const st of n.body.body) if (st.type === 'ReturnStatement') returned(st.argument);
  if (kind && !FACTORIES[b.name]) learned.factories[b.name] = kind;
  return kind;
}

// ---------- navigator usages inside a component ----------
const NODES = new Map(); // id -> {id, kind, drawer, file, line, label, screens: [], children: Set}
function nodeFor(nav, file, line, label) {
  const id = `${rel(file)}:${line}`;
  if (!NODES.has(id)) NODES.set(id, { id, kind: nav.kind, drawer: nav.drawer, factory: nav.factory, file, line, label, screens: [], children: new Set(), parents: 0, done: false });
  return NODES.get(id);
}

function screenComponentFromExpr(file, e, ctx) {
  e = unwrapExpr(e);
  if (!e) return { ref: null };
  // helper parameters: getScreens(options) { ... component={options.X} } / getScreens({ X }) { ... component={X} }
  const bound = ctx && (e.type === 'MemberExpression' && !e.computed && e.object.type === 'Identifier' && e.object.name === ctx.param ? ctx.props.get(e.property.name)
    : e.type === 'Identifier' && ctx.destructured.has(e.name) ? ctx.props.get(e.name) : null);
  if (bound) return screenComponentFromExpr(bound.file, bound.expr);
  if (e.type === 'Identifier') return { ref: { file, name: e.name } };
  if (e.type === 'MemberExpression' && !e.computed) return { ref: null, label: srcText(e), why: 'member expression' };
  if (e.type === 'ArrowFunctionExpression' || e.type === 'FunctionExpression') {
    let found = null;
    walk(e.body, (x) => { if (!found && x.type === 'JSXOpeningElement' && isComp(jsxName(x.name)) && !jsxName(x.name).includes('.')) { found = jsxName(x.name); return false; } });
    let req = null;
    walk(e.body, (x) => { if (!req && x.type === 'CallExpression' && x.callee.name === 'require' && x.arguments[0]?.type === 'StringLiteral') req = x.arguments[0].value; });
    if (req) return { ref: { file, module: req }, lazy: true };
    return { ref: found ? { file, name: found } : null, inline: true };
  }
  return { ref: null, why: e.type };
}
function srcText(e) {
  if (e.type === 'Identifier') return e.name;
  if (e.type === 'MemberExpression' && !e.computed) return `${srcText(e.object)}.${e.property.name}`;
  if (e.type === 'StringLiteral') return e.value;
  if (e.type === 'TemplateLiteral') return e.quasis.map((q) => q.value.cooked).join('${}');
  return `<${e.type}>`;
}
const attr = (el, name) => el.openingElement.attributes.find((a) => a.type === 'JSXAttribute' && a.name.name === name);
const attrExpr = (a) => (a?.value?.type === 'JSXExpressionContainer' ? a.value.expression : a?.value);

// Array literal behind an identifier (local or imported), for `routes.map(r => <S.Screen/>)`.
function arrayLength(file, e) {
  e = unwrapExpr(e);
  if (e?.type === 'ArrayExpression') return e.elements.length;
  if (e?.type === 'CallExpression' && e.callee.type === 'MemberExpression' && ['keys', 'values', 'entries'].includes(e.callee.property.name) && calleeName(e.callee.object) === 'Object') {
    const o = unwrapExpr(e.arguments[0]);
    if (o?.type === 'ObjectExpression') return o.properties.length;
    if (o?.type === 'Identifier') { const b = lookup(file, o.name); const v = b?.node && unwrapExpr(b.node); if (v?.type === 'ObjectExpression') return v.properties.length; }
  }
  if (e?.type === 'CallExpression' && e.callee.type === 'MemberExpression' && ['filter', 'slice'].includes(e.callee.property.name)) return arrayLength(file, e.callee.object);
  if (e?.type === 'Identifier') { const b = lookup(file, e.name); if (b?.node) return arrayLength(b.file, b.node); }
  return null;
}

function collectScreens(file0, navEl, objName, node) {
  const seenHelpers = new Set();
  const visit = (child, file, ctx) => {
    if (!child) return;
    if (child.type === 'JSXElement') {
      const n = jsxName(child.openingElement.name);
      if (n?.endsWith('.Screen')) return addScreen(child, 1, false, file, ctx);
      if (n?.endsWith('.Navigator')) return; // a nested navigator's own screens
      return child.children.forEach((c) => visit(c, file, ctx)); // Group, Fragment, wrappers
    }
    if (child.type === 'JSXFragment') return child.children.forEach((c) => visit(c, file, ctx));
    if (child.type === 'JSXExpressionContainer') return visitExpr(child.expression, file, ctx);
  };
  const visitExpr = (e, file, ctx) => {
    e = unwrapExpr(e);
    if (!e) return;
    if (e.type === 'JSXElement' || e.type === 'JSXFragment') return visit(e, file, ctx);
    if (e.type === 'LogicalExpression') return visitExpr(e.right, file, ctx);
    if (e.type === 'ConditionalExpression') { visitExpr(e.consequent, file, ctx); return visitExpr(e.alternate, file, ctx); }
    if (e.type === 'ArrayExpression') return e.elements.forEach((x) => visitExpr(x, file, ctx));
    if (e.type === 'CallExpression' && e.callee.type === 'MemberExpression' && e.callee.property.name === 'map') {
      let tmpl = null;
      walk(e.arguments[0], (x) => { if (!tmpl && x.type === 'JSXElement' && jsxName(x.openingElement.name)?.endsWith('.Screen')) { tmpl = x; return false; } });
      if (!tmpl) return;
      const key = `${rel(file)}#${srcText(e.callee.object)}`;
      let count = adapter.screenLists?.[key] ?? arrayLength(file, e.callee.object);
      if (count == null) { count = UNKNOWN_LIST; note('dynamic-screen-list', file, e.loc.start.line, `${srcText(e.callee.object)}.map(...) -> Screen`, `adapter.screenLists["${key}"] = <number of routes>`); }
      return addScreen(tmpl, count, true, file, ctx);
    }
    // Screen helper: {getAuthScreens(flag)} returning <Stack.Group>/<Stack.Screen>s (local or imported)
    if (e.type === 'CallExpression' && e.callee.type === 'Identifier') {
      const b = lookup(file, e.callee.name);
      const fn = b?.node;
      if (!fn || !/Function/.test(fn.type) || seenHelpers.has(b.file + '#' + b.name)) return;
      seenHelpers.add(b.file + '#' + b.name);
      const hctx = { param: null, destructured: new Set(), props: new Map() };
      const p0 = fn.params[0], a0 = unwrapExpr(e.arguments[0]);
      if (p0?.type === 'Identifier') hctx.param = p0.name;
      if (p0?.type === 'ObjectPattern') p0.properties.forEach((q) => q.value?.type === 'Identifier' && hctx.destructured.add(q.value.name));
      if (a0?.type === 'ObjectExpression') for (const q of a0.properties)
        if (q.type === 'ObjectProperty') hctx.props.set(q.key.name ?? q.key.value, { file, expr: q.value });
      if (fn.body.type !== 'BlockStatement') return visitExpr(fn.body, b.file, hctx);
      walk(fn.body, (x) => { if (x.type === 'ReturnStatement') visitExpr(x.argument, b.file, hctx); if (/Function/.test(x.type) && x !== fn) return false; });
    }
  };
  const addScreen = (el, count, dynamic, file, ctx) => {
    const nameA = attrExpr(attr(el, 'name'));
    const name = nameA ? srcText(nameA) : '?';
    const c = attr(el, 'component'), g = attr(el, 'getComponent');
    let comps = [];
    const ce = c && unwrapExpr(attrExpr(c));
    if (ce?.type === 'ConditionalExpression') comps = [screenComponentFromExpr(file, ce.consequent, ctx), screenComponentFromExpr(file, ce.alternate, ctx)];
    else if (c) comps = [screenComponentFromExpr(file, attrExpr(c), ctx)];
    else if (g) comps = [screenComponentFromExpr(file, attrExpr(g), ctx)];
    else {
      const fn = el.children.find((x) => x.type === 'JSXExpressionContainer' && /Function/.test(x.expression.type));
      if (fn) comps = [{ ...screenComponentFromExpr(file, fn.expression, ctx), inline: false }];
    }
    if (!comps.length) comps = [{ ref: null }];
    if (c && comps.some((x) => x.inline)) note('inline-component', file, el.loc.start.line, `component={() => ...} for "${name}" remounts the screen on every navigator render`, 'move the component out of render (performance fix, not an adapter key)');
    comps.forEach((comp, i) => node.screens.push({ name, count: i ? 0 : count, dynamic, ref: comp.ref, lazy: !!comp.lazy, inline: !!comp.inline, line: el.loc.start.line, file, why: comp.why }));
  };
  navEl.children.forEach((c) => visit(c, file0));
}

// Scan one component body for <X.Navigator> usages (JSX API).
const componentNodes = new Map(); // component key -> [node]
function navsInComponent(comp) {
  const key = comp.file + '#' + comp.name;
  if (componentNodes.has(key)) return componentNodes.get(key);
  componentNodes.set(key, []);
  const found = [];
  walk(comp.node, (x) => {
    if (x.type !== 'JSXElement') return;
    const n = jsxName(x.openingElement.name);
    if (!n?.endsWith('.Navigator')) return;
    const obj = n.slice(0, -'.Navigator'.length);
    const nav = classify(lookup(comp.file, obj.split('.')[0]));
    if (nav?.type !== 'nav') { note('unknown-navigator', comp.file, x.loc.start.line, `${n}: "${obj}" is not a known navigator factory result`, `adapter.factories["<factory or wrapper name>"] = "stack" | "tabs"`); return; }
    const node = nodeFor(nav, comp.file, x.loc.start.line, `${obj} in ${comp.name}`);
    if (!node.done) { node.done = true; collectScreens(comp.file, x, obj, node); }
    found.push(node);
  });
  componentNodes.set(key, found);
  return found;
}

// Static config API: createXNavigator({ screens: { A: Comp, B: { screen: Comp }, C: OtherNavigator } }).
function staticNode(nav) {
  const node = nodeFor(nav, nav.file, nav.line, nav.key.split('#')[1]);
  if (node.done) return node;
  node.done = true;
  const addFrom = (screensObj) => {
    for (const p of screensObj.properties) {
      if (p.type !== 'ObjectProperty') continue;
      let v = unwrapExpr(p.value);
      if (v.type === 'ObjectExpression') v = unwrapExpr(v.properties.find((q) => q.key?.name === 'screen')?.value);
      if (v?.type === 'Identifier') { const c = classify(lookup(nav.file, v.name)); if (c?.type === 'nav' && c.staticCfg) { node.screens.push({ name: p.key.name ?? p.key.value, count: 1, ref: null, staticNav: c }); continue; } }
      const name = p.key.name ?? p.key.value;
      if (v?.type === 'CallExpression' && factories[calleeName(v.callee)]) {
        const inner = { type: 'nav', key: `${nav.file}#${name}`, kind: factories[calleeName(v.callee)], drawer: DRAWER.has(calleeName(v.callee)), file: nav.file, line: v.loc.start.line, staticCfg: v.arguments[0] };
        node.screens.push({ name, count: 1, ref: null, staticNav: inner });
      } else node.screens.push({ name, count: 1, ref: v?.type === 'Identifier' ? { file: nav.file, name: v.name } : null });
    }
  };
  const cfg = nav.staticCfg;
  const screens = cfg.properties.find((p) => p.key?.name === 'screens');
  if (screens?.value.type === 'ObjectExpression') addFrom(screens.value);
  else if (!cfg.properties.some((p) => ['groups', 'initialRouteName', 'screenOptions'].includes(p.key?.name))) { addFrom(cfg); node.legacy = true; } // v4 route map
  const groups = cfg.properties.find((p) => p.key?.name === 'groups');
  if (groups?.value.type === 'ObjectExpression') for (const g of groups.value.properties) {
    const s = g.value.properties?.find((p) => p.key?.name === 'screens');
    if (s?.value.type === 'ObjectExpression') addFrom(s.value);
  }
  return node;
}

// Which navigators does rendering this screen component produce? Follows wrappers/routers up to `max` hops.
function navsRenderedBy(ref, hops = 0, seen = new Set()) {
  if (!ref || hops > 4) return [];
  let b;
  if (ref.module) { const f = resolveModule(ref.file, ref.module); if (!f) return []; if (!FILES.has(f)) indexFile(f); b = lookup(f, 'default'); }
  else b = lookup(ref.file, ref.name);
  const c = classify(b);
  if (!c) return [];
  if (c.type === 'nav') return c.staticCfg ? [staticNode(c)] : [];
  if (c.type !== 'component') return [];
  const key = c.file + '#' + c.name;
  if (seen.has(key)) return [];
  seen.add(key);
  const own = navsInComponent(c);
  if (own.length) return own;
  const out = [];
  walk(c.node, (x) => {
    if (x.type === 'JSXOpeningElement') {
      const n = jsxName(x.name);
      if (isComp(n) && !n.includes('.')) out.push(...navsRenderedBy({ file: c.file, name: n }, hops + 1, seen));
    }
  });
  return [...new Set(out)];
}

// ---------- Expo Router (file-system navigators) ----------
function expoRouterTree() {
  const pkg = readJsonLoose(path.join(REPO, 'package.json')) || {};
  if (!{ ...pkg.dependencies, ...pkg.devDependencies }['expo-router']) return null;
  const appDir = ['app', 'src/app'].map((d) => path.join(REPO, d)).find((d) => fs.existsSync(path.join(d, '_layout.tsx')) || fs.existsSync(path.join(d, '_layout.js')));
  if (!appDir) return null;
  const build = (dir) => {
    const layout = tryFile(path.join(dir, '_layout'));
    let kind = null;
    if (layout) {
      const code = fs.readFileSync(layout, 'utf8');
      kind = /<(Tabs|NativeTabs)\b/.test(code) || /<Drawer\b/.test(code) ? 'tabs' : /<Stack\b/.test(code) ? 'stack' : null; // <Slot> = no navigator
    }
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    const routes = [], kids = [];
    for (const e of entries) {
      if (e.name.startsWith('_') || e.name.startsWith('+') || e.name.startsWith('.')) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        const sub = build(p);
        if (sub.node) { routes.push(e.name); kids.push(sub.node); }
        else { routes.push(...sub.routes); kids.push(...sub.kids); }
      } else if (EXTS.includes(path.extname(e.name)) && !TEST_FILE.test(e.name)) routes.push(e.name.replace(/\.[jt]sx?$/, ''));
    }
    if (!kind) return { node: null, routes, kids };
    const node = nodeFor({ kind, drawer: /<Drawer\b/.test(fs.readFileSync(layout, 'utf8')), factory: 'expo-router' }, layout, 1, `${rel(dir) || 'app'}/_layout`);
    node.screens = routes.map((r) => ({ name: r, count: 1 }));
    kids.forEach((k) => { node.children.add(k.id); k.parents++; });
    return { node, routes, kids };
  };
  return build(appDir);
}

// ---------- model ----------
const model = fs.existsSync(MODEL_PATH) ? JSON.parse(fs.readFileSync(MODEL_PATH, 'utf8')) : null;
const predictArm = (arm, t, f) => { const c = model?.arms?.[arm]?.coef?.[t]; return c ? c.base + c.per_navigator * f[0] + c.per_tab_route * f[1] + c.per_stack_screen * f[2] : null; };

// ---------- main ----------
const t0 = Date.now();
const files = listFiles();
files.forEach(indexFile);
const er = expoRouterTree();
for (const info of [...FILES.values()]) {
  for (const [name, d] of info.decls) {
    const c = classify({ file: info.file, name, ...d });
    if (c?.type === 'component') navsInComponent(c);
    if (c?.type === 'nav' && c.staticCfg && c.file === info.file) staticNode(c);
  }
}
// edges: screen component -> navigators it renders
for (const node of [...NODES.values()]) for (const s of node.screens) {
  const kids = s.staticNav ? [staticNode(s.staticNav)] : navsRenderedBy(s.ref);
  for (const k of kids) if (k !== node && !node.children.has(k.id)) { node.children.add(k.id); k.parents++; }
  if (!s.ref && !s.staticNav && s.why && s.why !== 'Identifier') note('unresolved-screen', s.file || node.file, s.line ?? node.line, `screen "${s.name}" component is a ${s.why}`, 'fine unless that screen renders a nested navigator; then add it to adapter.screenNavigators');
}
for (const [screenKey, navId] of Object.entries(adapter.screenNavigators || {})) {
  const [nodeId, screenName] = screenKey.split('>');
  const p = NODES.get(nodeId), k = NODES.get(navId);
  if (p && k && !p.children.has(k.id)) { p.children.add(k.id); k.parents++; void screenName; }
}
// Reachability: which navigators does the app entry actually render? (index -> registerComponent/registerRootComponent -> App ...)
function entryComponents() {
  const pkg = readJsonLoose(path.join(REPO, 'package.json')) || {};
  const cands = [pkg.main, 'index.js', 'index.ts', 'index.tsx', 'App.tsx', 'App.js', 'src/App.tsx', 'src/App.js'].filter(Boolean);
  const out = [];
  for (const c of cands) {
    const f = tryFile(path.resolve(REPO, c));
    if (!f) continue;
    if (!FILES.has(f)) indexFile(f);
    const info = FILES.get(f);
    if (!info) continue;
    for (const name of info.decls.keys()) out.push({ file: f, name });
    for (const name of info.imports.keys()) if (isComp(name)) out.push({ file: f, name });
  }
  // AppRegistry.registerComponent(name, () => App) / registerRootComponent(App), wherever the entry hides them
  for (const f of files) {
    const info = FILES.get(f);
    if (!info) continue;
    const code = fs.readFileSync(f, 'utf8');
    if (!/register(Root)?Component\s*\(/.test(code)) continue;
    // registration files are small: every capitalized identifier is an entry candidate (App, AppRoot = withX(App), ...)
    for (const name of new Set(code.match(/\b[A-Z][A-Za-z0-9_]*\b/g))) if (info.imports.has(name) || info.decls.has(name)) out.push({ file: f, name });
  }
  return out;
}
const reachable = new Set();
if (er?.node) NODES.forEach((n) => n.factory === 'expo-router' && reachable.add(n.id));
{
  const seenC = new Set();
  const visitC = (ref, depth) => {
    if (depth > 12) return;
    const c = classify(lookup(ref.file, ref.name));
    if (c?.type === 'nav' && c.staticCfg) return markR(staticNode(c));
    if (c?.type !== 'component') return;
    const key = c.file + '#' + c.name;
    if (seenC.has(key)) return;
    seenC.add(key);
    navsInComponent(c).forEach(markR);
    walk(c.node, (x) => {
      if (x.type === 'JSXOpeningElement') { const n = jsxName(x.name); if (isComp(n) && !n.includes('.')) visitC({ file: c.file, name: n }, depth + 1); }
      if (x.type === 'CallExpression' && ['createAppContainer', 'createStaticNavigation'].includes(calleeName(x.callee)) && x.arguments[0]?.type === 'Identifier') visitC({ file: c.file, name: x.arguments[0].name }, depth + 1);
    });
  };
  const markR = (n) => { if (!n || reachable.has(n.id)) return; reachable.add(n.id); n.children.forEach((id) => markR(NODES.get(id))); };
  entryComponents().forEach((r) => visitC(r, 0));
}
const routesOf = (n) => n.screens.reduce((a, s) => a + (s.count || 1), 0);
const roots = [...NODES.values()].filter((n) => n.parents === 0);
const entryKnown = reachable.size > 0;
const deadRoots = entryKnown ? roots.filter((r) => !reachable.has(r.id)) : [];
const scored = [], seenPath = new Set();
const dfs = (n, trail) => {
  if (trail.includes(n) || scored.length > 5000) return;
  const p = [...trail, n];
  if (n.kind === 'stack') {
    const sig = p.map((x) => x.id).join('>');
    if (!seenPath.has(sig)) {
      seenPath.add(sig);
      const tabRoutes = p.filter((x) => x.kind === 'tabs').reduce((a, x) => a + routesOf(x), 0);
      const f = [p.length, tabRoutes, STACK_LEN];
      const rn = predictArm('rn', 'render', f), nat = predictArm(model?.arms?.native_frames ? 'native_frames' : 'native', 'render', f);
      scored.push({ path: p.map((x) => `${x.kind}(${routesOf(x)})`).join(' > '), leaf: n.label, at: n.id, navigators: p.length, tabRoutes,
        js: predictArm('rn', 'js', f), rn, native: nat, lag: rn != null && nat != null ? rn - nat : null, ids: p.map((x) => x.id) });
    }
  }
  for (const id of n.children) dfs(NODES.get(id), p);
};
roots.filter((r) => !entryKnown || reachable.has(r.id)).forEach((r) => dfs(r, []));
scored.sort((a, b) => (b.lag ?? 0) - (a.lag ?? 0));

const flags = [];
for (const n of NODES.values()) {
  if (n.kind === 'tabs' && routesOf(n) > 10 && n.children.size) flags.push(`BIG ${n.drawer ? 'DRAWER' : 'TABS'}: ${n.label} (${n.id}) holds ${routesOf(n)} routes above ${n.children.size} nested navigator(s): every push below re-processes all of them`);
}
const deepest = scored.reduce((a, s) => Math.max(a, s.navigators), 0);
if (deepest >= 6) flags.push(`DEEP: ${deepest} navigators on the deepest path (each ~${model?.arms?.rn?.coef?.js?.per_navigator ?? 1.5}ms JS per push)`);
for (const u of unresolved.filter((u) => u.kind === 'inline-component')) flags.push(`INLINE COMPONENT: ${u.at} ${u.detail}`);
for (const r of deadRoots) flags.push(`NOT REACHED FROM ENTRY: ${r.label} (${r.id})${r.legacy ? ' [v4 static config]' : ''}: dead code, or mounted dynamically (adapter.screenNavigators can wire it)`);

// ---------- output ----------
const top = Number(opt('--top', 15));
const out = { repo: REPO, files: files.length, navigators: NODES.size, roots: roots.map((r) => r.id), model: model ? MODEL_PATH : null, assumedStackLength: STACK_LEN,
  expoRouter: !!er?.node, paths: scored.slice(0, 200), flags, unresolved, learned, ms: Date.now() - t0,
  tree: [...NODES.values()].map((n) => ({ id: n.id, label: n.label, kind: n.kind, drawer: n.drawer, routes: routesOf(n), children: [...n.children], parents: n.parents, screens: n.screens.map((s) => s.name) })) };
if (argv.includes('--json')) fs.writeFileSync(opt('--json'), JSON.stringify(out, null, 1));
if (argv.includes('--learn')) {
  const next = { ...adapter, learned: { ...(adapter.learned || {}), factories: { ...(adapter.learned?.factories || {}), ...learned.factories }, aliases: { ...(adapter.learned?.aliases || {}), ...learned.aliases }, at: new Date().toISOString() },
    todo: [...new Map(unresolved.filter((u) => u.kind !== 'inline-component').map((u) => [u.kind + u.at, { [u.kind]: u.at, detail: u.detail, fix: u.fix }])).values()].slice(0, 100) };
  fs.writeFileSync(ADAPTER_PATH, JSON.stringify(next, null, 2) + '\n');
}

const print = (n, indent, seen) => {
  if (indent > 24) return;
  const routes = routesOf(n);
  const mark = entryKnown && !reachable.has(n.id) ? '  (not reached from entry)' : '';
  console.log(`${'  '.repeat(indent)}${n.kind === 'tabs' ? (n.drawer ? 'drawer' : 'tabs') : 'stack'}(${routes}) ${n.label}  [${n.id}]${mark}`);
  if (seen.has(n.id)) { if (n.children.size) console.log(`${'  '.repeat(indent + 1)}(shared subtree, shown above)`); return; }
  seen.add(n.id);
  for (const id of n.children) print(NODES.get(id), indent + 1, seen);
};
console.log(`navscan ${REPO}\n  ${files.length} files, ${NODES.size} navigators (${entryKnown ? reachable.size + ' reached from the app entry' : 'entry not found'}), ${roots.length} root(s)${er?.node ? ', expo-router tree' : ''}, ${out.ms}ms` +
  `\n  model: ${model ? path.relative(process.cwd(), MODEL_PATH) : 'none (no predictions)'}; assumed leaf stack length ${STACK_LEN}\n`);
const seen = new Set();
roots.sort((a, b) => b.children.size - a.children.size).forEach((r) => print(r, 1, seen));
if (scored.length) {
  console.log(`\nstacks by predicted RN cost per push (top ${Math.min(top, scored.length)} of ${scored.length}):`);
  console.log('  lag vs native  RN render  JS/push  navs  tab-routes  path');
  for (const s of scored.slice(0, top))
    console.log(`  ${((s.lag >= 0 ? '+' : '') + (s.lag ?? 0).toFixed(0)).padStart(5)}ms (${((s.lag ?? 0) / 16.7).toFixed(1)}f)  ${(s.rn ?? 0).toFixed(0).padStart(6)}ms ${(s.js ?? 0).toFixed(0).padStart(6)}ms  ${String(s.navigators).padStart(4)}  ${String(s.tabRoutes).padStart(10)}  ${s.path}  <- ${s.leaf}`);
}
if (flags.length) console.log('\nflags:\n' + flags.map((f) => '  ' + f).join('\n'));
const byKind = unresolved.filter((u) => u.kind !== 'inline-component').reduce((a, u) => ((a[u.kind] ||= []).push(u), a), {});
if (Object.keys(byKind).length) {
  console.log('\nunresolved (fix via the adapter, then re-run; --learn writes these as todo):');
  for (const [k, list] of Object.entries(byKind)) {
    console.log(`  ${k} x${list.length}  -> ${list[0].fix}`);
    list.slice(0, 5).forEach((u) => console.log(`    ${u.at}  ${u.detail}`));
  }
}
if (Object.keys(learned.factories).length) console.log(`\nlearned factory wrappers: ${JSON.stringify(learned.factories)}${argv.includes('--learn') ? ' (written to adapter)' : ' (--learn to save)'}`);
