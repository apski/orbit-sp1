// node tests/scout.test.mjs: scan/try/revert on a throwaway repo, and a hard-linked (pnpm store) file stays untouched.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, linkSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import assert from 'node:assert/strict';
const scout = new URL('../scripts/scout.mjs', import.meta.url).pathname;
const t = mkdtempSync(join(tmpdir(), 'scout-'));
const lib = join(t, 'lib'), repo = join(t, 'repo'), pkg = join(repo, 'node_modules/fake-pkg');
mkdirSync(lib); mkdirSync(join(pkg, 'src'), { recursive: true }); mkdirSync(join(t, 'store'));
writeFileSync(join(pkg, 'package.json'), '{"version":"1.2.3"}');
writeFileSync(join(t, 'store/a.txt'), 'slow\n');
linkSync(join(t, 'store/a.txt'), join(pkg, 'src/a.txt')); // the pnpm store hard link
writeFileSync(join(lib, 'fast.patch'), 'diff --git a/src/a.txt b/src/a.txt\n--- a/src/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n-slow\n+fast\n');
writeFileSync(join(lib, 'catalog.json'), JSON.stringify({ patches: [
  { id: 'fake-fast', file: 'fast.patch', package: 'fake-pkg', tested: '1.2.3', category: 'perf', helps_when: 'x' },
  { id: 'absent', file: 'fast.patch', package: 'not-here', tested: '1', category: 'perf', helps_when: 'x' }] }));
const run = (...a) => execFileSync('node', [scout, ...a], { encoding: 'utf8', env: { ...process.env, PATCH_SCOUT_LIB: lib } });
const state = () => Object.fromEntries(JSON.parse(run('scan', repo, '--json')).rows.map(r => [r.id, r.state]));

assert.deepEqual(state(), { 'fake-fast': 'applies', absent: 'not installed' });
run('try', 'fake-fast', repo);
assert.equal(readFileSync(join(pkg, 'src/a.txt'), 'utf8'), 'fast\n');
assert.equal(readFileSync(join(t, 'store/a.txt'), 'utf8'), 'slow\n', 'store copy must not change');
assert.equal(state()['fake-fast'], 'already in');
assert.deepEqual(JSON.parse(readFileSync(join(repo, '.patch-scout/applied.json'), 'utf8')), ['fake-fast']);
run('revert', 'fake-fast', repo);
assert.equal(readFileSync(join(pkg, 'src/a.txt'), 'utf8'), 'slow\n');
assert.equal(state()['fake-fast'], 'applies');
console.log('ok  scan/try/revert, store link untouched');
