// Shared by navscan and arch/depgraph: JSON-with-comments reader and a module resolver that follows
// relative imports, tsconfig/jsconfig baseUrl + paths, babel module-resolver alias/root, and adapter aliases.
import fs from 'node:fs';
import path from 'node:path';

export const EXTS = ['.tsx', '.ts', '.jsx', '.js'];

// JSON with comments and trailing commas (tsconfig). String-aware: "app/*" is not a comment opener.
export function readJsonLoose(f) {
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

/** makeResolver(repo, adapter) -> { resolveModule(fromFile, spec), tryFile(base), aliases, bases, detectedAliases } */
export function makeResolver(REPO, adapter = {}) {
  const detected = { aliases: {}, bases: [] };
  const ts = readJsonLoose(path.join(REPO, 'tsconfig.json')) || readJsonLoose(path.join(REPO, 'jsconfig.json'));
  const co = ts?.compilerOptions || {};
  if (co.baseUrl) detected.bases.push(path.resolve(REPO, co.baseUrl));
  for (const [k, v] of Object.entries(co.paths || {})) {
    const from = k.replace(/\*$/, ''), to = String(v[0] || '').replace(/\*$/, '');
    if (from) detected.aliases[from] = path.relative(REPO, path.resolve(REPO, co.baseUrl || '.', to)) + (to.endsWith('/') ? '/' : '');
  }
  for (const f of ['babel.config.js', '.babelrc', '.babelrc.js', 'babel.config.json']) {
    const p = path.join(REPO, f);
    if (!fs.existsSync(p)) continue;
    const src = fs.readFileSync(p, 'utf8');
    const block = src.match(/alias\s*:\s*\{([^}]*)\}/);
    if (block) for (const m of block[1].matchAll(/['"]?([@~\w\-/.]+)['"]?\s*:\s*['"]([^'"]+)['"]/g)) detected.aliases[m[1].endsWith('/') ? m[1] : m[1] + '/'] = m[2].replace(/^\.\//, '') + '/';
    const root = src.match(/root\s*:\s*\[([^\]]*)\]/);
    if (root) for (const m of root[1].matchAll(/['"]([^'"]+)['"]/g)) detected.bases.push(path.resolve(REPO, m[1]));
  }
  const aliases = { ...detected.aliases, ...(adapter.learned?.aliases || {}), ...(adapter.aliases || {}) };
  const bases = [...new Set([...detected.bases, ...(adapter.baseUrl ? [path.resolve(REPO, adapter.baseUrl)] : [])])];
  function tryFile(base) {
    for (const cand of [...(EXTS.includes(path.extname(base)) ? [base] : []), ...EXTS.flatMap((e) => [base + e, base.replace(/(\.[jt]sx?)?$/, '.ios' + e)]), ...EXTS.map((e) => path.join(base, 'index' + e))])
      if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return cand;
    return null;
  }
  function resolveModule(from, src) {
    if (src.startsWith('.')) return tryFile(path.resolve(path.dirname(from), src));
    for (const [a, to] of Object.entries(aliases)) if (src === a.replace(/\/$/, '') || src.startsWith(a)) {
      const hit = tryFile(path.resolve(REPO, to, src.slice(a.length)));
      if (hit) return hit;
    }
    for (const b of bases) { const hit = tryFile(path.join(b, src)); if (hit) return hit; }
    return null; // package import
  }
  return { resolveModule, tryFile, aliases, bases, detectedAliases: detected.aliases };
}

// Shared parser settings (navscan, arch/facts): TS/TSX/Flow+JSX, error recovery so one bad file never stops a scan.
import { createRequire } from 'node:module';
const { parse: babelParse } = createRequire(import.meta.url)('@babel/parser');
export function parseCode(file, code) {
  const ext = path.extname(file);
  return babelParse(code, { sourceType: 'module', errorRecovery: true, allowReturnOutsideFunction: true,
    plugins: [ext === '.ts' ? 'typescript' : ext === '.tsx' ? ['typescript', { dts: false }] : 'flow', ...(ext === '.ts' ? [] : ['jsx']),
      'decorators-legacy', 'classProperties', 'exportDefaultFrom'] });
}

/** Generic AST walk: fn(node, parent) returning false skips the subtree. */
export function walk(node, fn, parent) {
  if (!node || typeof node.type !== 'string') return;
  if (fn(node, parent) === false) return;
  for (const k in node) {
    if (k === 'loc' || k === 'extra' || k.endsWith('Comments')) continue;
    const v = node[k];
    if (Array.isArray(v)) { for (const c of v) if (c && typeof c.type === 'string') walk(c, fn, node); }
    else if (v && typeof v.type === 'string') walk(v, fn, node);
  }
}
