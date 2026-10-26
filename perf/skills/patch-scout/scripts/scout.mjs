#!/usr/bin/env node
// patch-scout: match a repo's installed RN packages against a library of known patches (perf, crash, visual),
// dry-run each one, and apply/revert them locally in node_modules so perf-climb can A/B them.
//   scout.mjs scan   [repo] [--category perf|crash|bug|visual] [--json]
//   scout.mjs show   <id>
//   scout.mjs try    <id> [repo]      apply to node_modules (local experiment; recorded in <repo>/.patch-scout/)
//   scout.mjs revert <id> [repo]
// Library: ../library/catalog.json, or $PATCH_SCOUT_LIB/catalog.json.
import { existsSync, readFileSync, writeFileSync, mkdirSync, statSync, copyFileSync, renameSync, appendFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join, dirname, resolve } from 'node:path';

const LIB = process.env.PATCH_SCOUT_LIB || join(dirname(new URL(import.meta.url).pathname), '..', 'library');
const cat = JSON.parse(readFileSync(join(LIB, 'catalog.json'), 'utf8'));
const [cmd, ...rest] = process.argv.slice(2);
const flags = rest.filter(a => a.startsWith('--'));
const pos = rest.filter((a, i) => !a.startsWith('--') && !(rest[i - 1] || '').startsWith('--category'));
const opt = k => (rest.includes(`--${k}`) ? rest[rest.indexOf(`--${k}`) + 1] : undefined);
const die = m => { console.error(`patch-scout: ${m}`); process.exit(1); };
const byId = id => cat.patches.find(p => p.id === id) || die(`no patch ${id} (scout.mjs scan lists them)`);

const pkgDir = (repo, name) => join(repo, 'node_modules', name);
const version = (repo, name) => { try { return JSON.parse(readFileSync(join(pkgDir(repo, name), 'package.json'), 'utf8')).version; } catch { return null; } };
const patchCmd = (dir, file, ...extra) => spawnSync('patch', ['-p1', '-f', '-s', '-d', dir, '-i', join(LIB, file), ...extra], { encoding: 'utf8' });

function status(repo, p) {
  const v = version(repo, p.package);
  if (!v) return { v, state: 'not installed' };
  const dir = pkgDir(repo, p.package);
  if (!patchCmd(dir, p.file, '--dry-run').status) return { v, state: 'applies' };
  if (!patchCmd(dir, p.file, '--dry-run', '-R').status) return { v, state: 'already in' };
  return { v, state: 'conflicts' };
}

// rn-from-source patches do nothing while the app links the prebuilt React Native core
function prebuiltWarning(repo) {
  const app = ['app.config.js', 'app.config.ts', 'app.json'].map(f => join(repo, f)).find(existsSync);
  const fromSource = app && /buildReactNativeFromSource\s*["']?\s*:\s*true/.test(readFileSync(app, 'utf8'));
  const prebuilt = existsSync(join(repo, 'ios/Pods/React-Core-prebuilt')) || (!fromSource && Number((version(repo, 'react-native') || '0.0').split('.')[1]) >= 84);
  return prebuilt ? 'app links the prebuilt RN core: build BOTH baseline and candidate with RCT_USE_PREBUILT_RNCORE=0 (pod install) or the patch is not in the binary' : null;
}

// pnpm (and some yarn setups) hard-link node_modules files to a global store: give touched files their own inode
// first, or the patch edits that file for every project on the machine.
function unshare(dir, file) {
  for (const m of readFileSync(join(LIB, file), 'utf8').matchAll(/^\+\+\+ b\/(\S+)/gm)) {
    const f = join(dir, m[1]);
    if (existsSync(f) && statSync(f).nlink > 1) { copyFileSync(f, `${f}.scout-tmp`); renameSync(`${f}.scout-tmp`, f); }
  }
}

function record(repo, id, applied) {
  const d = join(repo, '.patch-scout');
  mkdirSync(d, { recursive: true });
  const f = join(d, 'applied.json');
  const s = new Set(existsSync(f) ? JSON.parse(readFileSync(f, 'utf8')) : []);
  applied ? s.add(id) : s.delete(id);
  writeFileSync(f, JSON.stringify([...s], null, 1));
  const ex = join(repo, '.git/info/exclude'); // local-only: never shows up in git status
  if (existsSync(ex) && !readFileSync(ex, 'utf8').includes('.patch-scout/')) appendFileSync(ex, '\n.patch-scout/\n');
}

if (cmd === 'scan') {
  const repo = resolve(pos[0] || '.');
  const rows = cat.patches.filter(p => !opt('category') || p.category === opt('category')).map(p => ({ ...p, ...status(repo, p) }));
  const warn = rows.some(r => r.state === 'applies' && r.needs?.includes('rn-from-source')) && prebuiltWarning(repo);
  if (flags.includes('--json')) { console.log(JSON.stringify({ repo, warn, rows: rows.map(({ id, category, package: pkg, v, tested, state }) => ({ id, category, package: pkg, installed: v, tested, state })) }, null, 1)); process.exit(0); }
  const order = { applies: 0, conflicts: 1, 'already in': 2, 'not installed': 3 };
  rows.sort((a, b) => order[a.state] - order[b.state] || (a.category === 'perf' ? -1 : 1));
  console.log(`patch-scout  ${repo}  (${cat.patches.length} patches in library)\n`);
  for (const r of rows.filter(r => r.state !== 'not installed'))
    console.log(`${r.state.padEnd(11)} [${r.category}] ${r.id}  ${r.package}@${r.v} (tested ${r.tested})\n            ${r.helps_when}${r.measured ? `\n            measured: ${r.measured}` : ''}\n`);
  const missing = rows.filter(r => r.state === 'not installed').length;
  if (missing) console.log(`(${missing} patches target packages this repo doesn't have)`);
  if (warn) console.log(`\nnote: ${warn}`);
} else if (cmd === 'show') {
  const p = byId(pos[0]);
  console.log(JSON.stringify(p, null, 1));
  if (existsSync(join(LIB, `${p.file}.md`))) console.log(`\n${readFileSync(join(LIB, `${p.file}.md`), 'utf8')}`);
} else if (cmd === 'try' || cmd === 'revert') {
  const p = byId(pos[0]), repo = resolve(pos[1] || '.'), dir = pkgDir(repo, p.package);
  const s = status(repo, p);
  if (cmd === 'try' && s.state !== 'applies') die(`${p.id} ${s.state} on ${p.package}@${s.v}`);
  if (cmd === 'revert' && s.state !== 'already in') die(`${p.id} is not applied (${s.state})`);
  unshare(dir, p.file);
  const r = patchCmd(dir, p.file, ...(cmd === 'revert' ? ['-R'] : []));
  if (r.status) die(`patch failed: ${r.stdout}${r.stderr}`);
  record(repo, p.id, cmd === 'try');
  console.log(`${cmd === 'try' ? 'applied' : 'reverted'} ${p.id} in ${p.package}@${s.v}`);
  if (cmd === 'try') {
    if (p.needs?.includes('rn-from-source')) { const w = prebuiltWarning(repo); if (w) console.log(`note: ${w}`); }
    if (p.measure) console.log(`measure: ${p.measure.scenario} -> ${p.measure.metric}`);
    console.log('node_modules was changed in place: a reinstall drops it; adopt with patch-package / pnpm patch only after perf-climb accepts');
  }
} else {
  die('usage: scout.mjs scan [repo] [--category perf] [--json] | show <id> | try <id> [repo] | revert <id> [repo]');
}
