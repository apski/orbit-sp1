#!/usr/bin/env node
// depgraph: the as-is import structure of one feature.
//
//   node depgraph.mjs <repo> --entry <file> [--entry <file> ...] [--own <dir|file> ...] [--json out.json] [--max-depth 8]
//   node depgraph.mjs <repo> --edges out.json        every local import edge in the repo ([from, to], repo-relative)
//   node depgraph.mjs --selftest
//
// Builds the repo-wide local import graph (same resolver as navscan: tsconfig paths, babel aliases, baseUrl), then the
// feature's closure from its entry points (usually its navigator/screen files from navscan). Every file in the closure
// is **owned** (only reached from inside the feature) or **shared** (also imported from outside: a dependency, not part
// of the feature). Reports cycles (Tarjan SCC), fan-in/out, LOC and the shared surface the feature leans on.
// --own: the feature's own code even when outside files import it (its state core, components other features embed).
// Without it a feature's store drops out of scope as "shared" the moment a badge elsewhere reads it. Owned files that
// outside files import are flagged `public` (the feature's interface; outsideImporters = how many).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { EXTS, makeResolver } from '../scan/lib.mjs';

if (process.argv[2] === '--selftest') {  // fixture: state/f/core.ts is the feature's store, also read by other/Badge.tsx
  const me = fileURLToPath(import.meta.url), fx = path.join(path.dirname(me), 'fixtures', 'depgraph');
  const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'depgraph-')), 'out.json');
  const run = (...extra) => (spawnSync('node', [me, fx, '--entry', 'screens/F/Screen.tsx', '--json', tmp, ...extra]), JSON.parse(fs.readFileSync(tmp, 'utf8')));
  const plain = run().owned.map((o) => o.file), own = run('--own', 'state/f').owned;
  const ok = !plain.includes('state/f/core.ts') && own.find((o) => o.file === 'state/f/core.ts')?.public === true
    && own.some((o) => o.file === 'state/f/unread.ts') && !own.some((o) => o.file === 'other/Badge.tsx');
  console.log(ok ? 'selftest ok' : `selftest FAIL: plain ${plain}; own ${JSON.stringify(own)}`);
  process.exit(ok ? 0 : 1);
}

const argv = process.argv.slice(2);
const opts = (k) => argv.flatMap((a, i) => (argv[i - 1] === k ? [a] : []));
const opt = (k, d) => opts(k)[0] ?? d;
const REPO = path.resolve(argv.find((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--')) || '.');
const adapterPath = path.join(REPO, 'navlab.adapter.json');
const adapter = fs.existsSync(adapterPath) ? JSON.parse(fs.readFileSync(adapterPath, 'utf8')) : {};
const { resolveModule, tryFile } = makeResolver(REPO, adapter);
const SKIP = new Set(['node_modules', 'ios', 'android', '.git', 'build', 'dist', 'coverage', '.expo', 'Pods', 'vendor']);
const TEST = /\.(test|spec|stories|story|mock|e2e)\.[jt]sx?$|__tests__|__mocks__/;
const rel = (f) => path.relative(REPO, f);

// ---------- repo-wide import graph (regex: imports, re-exports, require, dynamic import) ----------
const files = [];
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.name.startsWith('.') || SKIP.has(e.name)) continue;
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (EXTS.includes(path.extname(e.name)) && !e.name.endsWith('.d.ts')) files.push(p);
  }
})(REPO);
const IMPORT = /(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|import\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)|import\(\s*['"]([^'"]+)['"]\s*\)/g;
const out = new Map(), inn = new Map(), loc = new Map();
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  loc.set(f, src.split('\n').length);
  const deps = new Set();
  for (const m of src.matchAll(IMPORT)) {
    const spec = m[1] || m[2] || m[3] || m[4];
    const t = resolveModule(f, spec);
    if (t && t !== f) deps.add(t);
  }
  out.set(f, deps);
  for (const t of deps) (inn.get(t) || inn.set(t, new Set()).get(t)).add(f);
}

// files reached by resolution but outside the walk (platform variants, generated files) still get a line count
const locOf = (f) => loc.get(f) ?? (fs.existsSync(f) ? fs.readFileSync(f, 'utf8').split('\n').length : 0);

// ---------- feature closure ----------
function closureOf(entries, maxDepth) {
  const depth = new Map(entries.map((e) => [e, 0]));
  const queue = [...entries];
  while (queue.length) {
    const f = queue.shift();
    if (depth.get(f) >= maxDepth) continue;
    for (const t of out.get(f) || []) if (!depth.has(t)) { depth.set(t, depth.get(f) + 1); queue.push(t); }
  }
  const closure = new Set(depth.keys());
  // owned: every importer is inside the closure (or it is an entry); tests don't count as outside users
  const owned = new Set(closure);
  let changed = true;
  while (changed) {
    changed = false;
    for (const f of [...owned]) {
      if (entries.includes(f)) continue;
      if ([...(inn.get(f) || [])].some((i) => !TEST.test(i) && (!closure.has(i) || !owned.has(i)))) { owned.delete(f); changed = true; }
    }
  }
  return { depth, closure, owned };
}

// --features <json>: {"name": ["entry", ...]} -> per-feature owned files in one pass over the repo (portfolio.py)
if (opts('--features').length) {
  const spec = JSON.parse(fs.readFileSync(opt('--features'), 'utf8'));
  const res = {};
  for (const [name, ents] of Object.entries(spec)) {
    const e = ents.map((x) => tryFile(path.resolve(REPO, x)) || path.resolve(REPO, x));
    const { owned, closure } = closureOf(e, Number(opt('--max-depth', 8)));
    res[name] = { entries: ents, owned: [...owned].map((f) => ({ file: rel(f), loc: locOf(f) })), shared: closure.size - owned.size };
  }
  fs.writeFileSync(opt('--json', '/dev/stdout'), JSON.stringify(res));
  process.exit(0);
}

if (opts('--edges').length) {
  fs.writeFileSync(opt('--edges'), JSON.stringify([...out].flatMap(([f, ts]) => [...ts].map((t) => [rel(f), rel(t)]))));
  process.exit(0);
}

const ownDirs = opts('--own').map((p) => path.resolve(REPO, p));
const ownFiles = files.filter((f) => !TEST.test(f) && ownDirs.some((d) => f === d || f.startsWith(d + path.sep)));
const entries = [...new Set([...opts('--entry').map((e) => tryFile(path.resolve(REPO, e)) || path.resolve(REPO, e)), ...ownFiles])];
if (!entries.length) { console.error('give at least one --entry <file> (a navigator or screen file of the feature) or --own <dir>, or --features <json>'); process.exit(1); }
const { depth, closure, owned } = closureOf(entries, Number(opt('--max-depth', 8)));
const shared = [...closure].filter((f) => !owned.has(f));

// ---------- cycles among owned files (Tarjan SCC) ----------
const idx = new Map(), low = new Map(), onStack = new Set(), stack = [], sccs = [];
let n = 0;
function strong(v) {
  idx.set(v, n); low.set(v, n); n++; stack.push(v); onStack.add(v);
  for (const w of out.get(v) || []) {
    if (!owned.has(w)) continue;
    if (!idx.has(w)) { strong(w); low.set(v, Math.min(low.get(v), low.get(w))); }
    else if (onStack.has(w)) low.set(v, Math.min(low.get(v), idx.get(w)));
  }
  if (low.get(v) === idx.get(v)) {
    const c = [];
    let w;
    do { w = stack.pop(); onStack.delete(w); c.push(w); } while (w !== v);
    if (c.length > 1) sccs.push(c);
  }
}
for (const f of owned) if (!idx.has(f)) strong(f);

// ---------- report ----------
const ownedList = [...owned].sort((a, b) => loc.get(b) - loc.get(a));
const dirs = {};
for (const f of owned) { const d = path.dirname(rel(f)); dirs[d] = (dirs[d] || 0) + 1; }
const sharedUse = shared.map((f) => ({ file: rel(f), usedBy: [...(inn.get(f) || [])].filter((i) => owned.has(i)).length, repoFanIn: (inn.get(f) || new Set()).size, loc: loc.get(f) }))
  .sort((a, b) => b.usedBy - a.usedBy);
const result = {
  repo: REPO, entries: entries.map(rel), files: files.length,
  owned: ownedList.map((f) => {
    const outside = [...(inn.get(f) || [])].filter((i) => !owned.has(i) && !TEST.test(i)).length;
    return { file: rel(f), loc: locOf(f), fanOut: [...(out.get(f) || [])].length, fanIn: (inn.get(f) || new Set()).size, depth: depth.get(f), ...(outside && { public: true, outsideImporters: outside }) };
  }),
  shared: sharedUse,
  cycles: sccs.map((c) => c.map(rel)),
  dirs,
  edges: [...owned].flatMap((f) => [...(out.get(f) || [])].filter((t) => closure.has(t)).map((t) => [rel(f), rel(t)])),
};
if (opts('--json').length) fs.writeFileSync(opt('--json'), JSON.stringify(result, null, 1));
const ownedLoc = ownedList.reduce((a, f) => a + locOf(f), 0);
console.log(`depgraph ${rel(entries[0])}${entries.length > 1 ? ` (+${entries.length - 1} entries)` : ''}: ${owned.size} owned files (${ownedLoc} LOC) in ${Object.keys(dirs).length} dirs, ` +
  `${shared.length} shared dependencies, ${sccs.length} import cycle(s); repo ${files.length} files`);
console.log('\nowned, largest first (loc, fan-in, fan-out):');
ownedList.slice(0, 15).forEach((f) => console.log(`  ${String(loc.get(f)).padStart(5)} ${String((inn.get(f) || new Set()).size).padStart(3)} ${String((out.get(f) || new Set()).size).padStart(3)}  ${rel(f)}`));
console.log('\nowned files by directory:');
Object.entries(dirs).sort((a, b) => b[1] - a[1]).slice(0, 15).forEach(([d, c]) => console.log(`  ${String(c).padStart(4)}  ${d}`));
console.log('\nshared dependencies the feature leans on most (used by N owned files; repo-wide fan-in):');
sharedUse.slice(0, 12).forEach((s) => console.log(`  ${String(s.usedBy).padStart(4)} ${String(s.repoFanIn).padStart(5)}  ${s.file}`));
if (sccs.length) {
  console.log('\nimport cycles among owned files:');
  sccs.slice(0, 8).forEach((c) => console.log(`  ${c.length} files: ${c.slice(0, 4).map(rel).join(' -> ')}${c.length > 4 ? ' ...' : ''}`));
}
