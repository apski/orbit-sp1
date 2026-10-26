// node tests/audit.test.mjs: every detector fires on the fixture, and gated/echo-only phases stay quiet.
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
const dir = new URL('.', import.meta.url).pathname;
const out = JSON.parse(execFileSync('node', [`${dir}../scripts/audit.mjs`, `${dir}fixture`, '--json'], { encoding: 'utf8' }));
const ids = out.findings.map(f => f.id);
for (const id of ['ios-debug-dsym', 'ios-upload-in-debug', 'ios-compilation-cache-off', 'ios-flipper', 'metro-reset-cache', 'android-all-abis', 'android-caching'])
  assert(ids.includes(id), `missing ${id}: ${ids.join(', ')}`);
assert.equal(ids.filter(i => i === 'ios-upload-in-debug').length, 1, 'gated upload phase must not be flagged');
assert(!out.findings.some(f => /Disabled|Gated/.test(f.where)), 'echo-only and gated phases must not be flagged');
assert.equal(ids.filter(i => i === 'metro-reset-cache').length, 2, 'package.json script and metro.config both flagged');
console.log(`ok  ${ids.length} findings`);

// Expo CNG: config-level checks, and from-source is explained by the native RN patch
const expo = JSON.parse(execFileSync('node', [`${dir}../scripts/audit.mjs`, `${dir}fixture-expo`, '--json'], { encoding: 'utf8' })).findings;
for (const id of ['expo-cng', 'expo-rn-from-source', 'expo-build-cache-eas', 'expo-prebuild-clean', 'metro-reset-cache', 'ios-ccache-and-cas'])
  assert(expo.some(f => f.id === id), `expo fixture missing ${id}`);
assert.match(expo.find(f => f.id === 'expo-rn-from-source').why, /1 RN native files/);
console.log(`ok  expo ${expo.length} findings`);
