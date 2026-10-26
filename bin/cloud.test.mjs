// node --test bin/cloud.test.mjs: the pure parts of cloud.mjs (no doctl, no network)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { expiry, ours, shellCmd, ttlSeconds } from './cloud.mjs';

test('ttl', () => {
  assert.equal(ttlSeconds('30m'), 1800);
  assert.equal(ttlSeconds('2h'), 7200);
  assert.equal(ttlSeconds('1d'), 86400);
  assert.throws(() => ttlSeconds('2 hours'));
  assert.throws(() => ttlSeconds(undefined));
});

test('only orbit-cloud boxes are ours: name prefix AND tag', () => {
  assert.ok(ours({ name: 'orbit-cloud-default', tags: ['orbit-cloud'] }));
  assert.ok(!ours({ name: 'prod1', tags: ['orbit-cloud'] }));
  assert.ok(!ours({ name: 'orbit-cloud-default', tags: [] }));
  assert.ok(!ours({ name: 'orbit-cloud-default' }));
});

test('expiry from tags; none (or garbage) means expired', () => {
  assert.equal(expiry({ tags: ['orbit-cloud', 'orbit-cloud-exp-1761500000'] }), 1761500000);
  assert.equal(expiry({ tags: ['orbit-cloud-exp-100', 'orbit-cloud-exp-200'] }), 200);
  assert.equal(expiry({ tags: ['orbit-cloud'] }), 0);
  assert.equal(expiry({ tags: ['orbit-cloud-exp-soon'] }), 0);
});

test('one arg is a shell string, several are quoted argv', () => {
  assert.equal(shellCmd(['npm ci && npm test']), 'npm ci && npm test');
  assert.equal(shellCmd(['pytest', '-k', 'a or b']), "pytest -k 'a or b'");
  assert.equal(shellCmd(['echo', "it's"]), "echo 'it'\\''s'");
});

test('auto keeps macOS work local, sends the rest out', async () => {
  const { needsMac } = await import('./cloud.mjs');
  for (const c of ['xcrun simctl list', 'xcodebuild -scheme App build', 'npx expo run:ios', 'simwalk flows/a.mjs',
    'orbitfleet claim', 'spectra verify Home', 'orbit sim claim', 'pod install', 'argent-sim slim', 'swift build'])
    assert.ok(needsMac(c), c);
  for (const c of ['npm test', 'python3 -m unittest discover -s tests', 'cargo build --release', '.orbit/orbit index',
    'voyager backtest', 'jest --ci', 'node --test bin/*.test.mjs', 'spectra install .', 'orbit verify gate'])
    assert.equal(needsMac(c), null, c);
});
