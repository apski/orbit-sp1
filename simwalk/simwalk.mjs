#!/usr/bin/env node
// simwalk <flow.mjs> [--udid <UDID>] [--repeat N]
// Runs a flow, times each step, and on failure prints the screen outline + saves a screenshot.
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { connect } from './driver.mjs';

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args.splice(i, 2)[1] : dflt; };
const udid = opt('udid') ?? firstBootedIphone();
const repeat = +opt('repeat', 1);
const flowPath = args[0];
if (!flowPath || !udid) {
  console.error('usage: simwalk <flow.mjs> [--udid <UDID>] [--repeat N]   (needs a booted simulator)');
  process.exit(2);
}

function firstBootedIphone() {
  const { devices } = JSON.parse(execFileSync('xcrun', ['simctl', 'list', 'devices', 'booted', '-j']));
  return Object.values(devices).flat().find((d) => d.state === 'Booted')?.udid;
}

const flow = await import(pathToFileURL(path.resolve(flowPath)));
const d = await connect(udid);
let current = '';
const step = async (name, fn) => {
  current = name;
  const t = performance.now();
  await fn();
  console.log(`  ${(performance.now() - t).toFixed(0).padStart(6)}ms  ${name}`);
};

let code = 0;
try {
  for (let i = 1; i <= repeat; i++) {
    console.log(`${flow.name ?? path.basename(flowPath)}  run ${i}/${repeat}`);
    const t = performance.now();
    await flow.default({ d, step });
    console.log(`  ${(performance.now() - t).toFixed(0).padStart(6)}ms  TOTAL${d.respawns ? `  (ax-service respawns: ${d.respawns})` : ''}`);
  }
} catch (e) {
  code = 1;
  const shot = path.resolve(`simwalk-fail-${Date.now()}.png`);
  console.error(`\nFAILED at step "${current}": ${e.message}\n\nScreen outline (id | label | value):\n${await d.outline().catch(() => '(unavailable)')}`);
  try { d.screenshot(shot); console.error(`\nscreenshot: ${shot}`); } catch {}
}
d.close();
process.exit(code);
