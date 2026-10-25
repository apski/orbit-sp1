// bootstrap self-check on fixtures (copied to a temp dir; bootstrap writes into the repo it's given). `node bootstrap.test.mjs`
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const here = path.dirname(new URL(import.meta.url).pathname);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'navlab-boot-'));
fs.cpSync(path.join(here, 'arch/fixtures/cng'), tmp, { recursive: true });
execFileSync('node', [path.join(here, 'bootstrap.mjs'), tmp, '--no-skills'], { stdio: 'ignore', env: { ...process.env, NAVLAB_GAPS: path.join(tmp, 'gaps.jsonl') } }); // never harvest fixtures into the pack ledger
const cfg = JSON.parse(fs.readFileSync(path.join(tmp, 'navlab.config.json'), 'utf8'));
assert.equal(cfg.bundleId, 'com.fixture.app', 'bundle id from app.config.js when ios/ is absent (g0035)');
assert.equal(cfg.process, 'Fixture', 'process name from app.config.js');
console.log('bootstrap self-check passed');
