// sim: which iOS simulator a pack tool may drive. One place, so every subsystem moves to slim sims together.
//
//   orbit sim claim [--holder <repo:tool>] [--udid <pinned>]   prints {"udid", "source", "lease"} as JSON
//   orbit sim release <udid> [--holder <repo:tool>]
//
// Mode (`argent-sim slim|full|status`, stored in ~/.argent/sim-mode, default slim):
//   slim  lease a slim simulator from orbitfleet (one agent, one sim; ~3-4x fewer processes than a regular sim)
//   full  the pinned udid, else the first booted iPhone (the old behaviour)
// A pinned udid (`--udid`, or a config value other than "fleet") always wins: a physical phone, a perf baseline sim.
import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const mode = () => { try { return readFileSync(join(homedir(), '.argent', 'sim-mode'), 'utf8').trim() || 'slim'; } catch { return 'slim'; } };
const has = bin => spawnSync('sh', ['-c', `command -v ${bin}`]).status === 0;

function booted() {
  const d = JSON.parse(execFileSync('xcrun', ['simctl', 'list', 'devices', 'booted', '-j']).toString()).devices;
  return Object.values(d).flat().find(x => /iPhone/.test(x.name))?.udid;
}

/** -> {udid, source: 'pinned'|'fleet'|'booted', lease: bool, holder} or throws with the reason. */
export function claim({ holder = 'orbit-sp1', udid = null } = {}) {
  if (udid && udid !== 'fleet') return { udid, source: 'pinned', lease: false, holder };
  if (mode() === 'slim' && has('orbitfleet')) {
    const r = spawnSync('orbitfleet', ['claim', '--holder', holder, '--wait', '10m', '--ttl', '60m', '--json'], { encoding: 'utf8' });
    if (r.status === 0) {
      const j = JSON.parse(r.stdout.slice(r.stdout.indexOf('{')));
      return { udid: j.udid, source: 'fleet', lease: true, holder, ...(j.warning && { warning: j.warning }) };
    }
    // no fleet-* pool on this Mac, or capacity after the wait: fall back, and say so
    process.stderr.write(`sim: orbitfleet claim failed (${(r.stdout + r.stderr).trim().split('\n').slice(-1)[0]}); falling back to a booted sim\n`);
  }
  const b = booted();
  if (!b) throw new Error('no simulator: no orbitfleet lease and no booted iPhone (boot one, or pass --udid)');
  return { udid: b, source: 'booted', lease: false, holder };
}

export function release(c) {
  if (c?.lease) spawnSync('orbitfleet', ['release', c.udid, '--holder', c.holder], { encoding: 'utf8' });
}

// CLI (also reached as `orbit sim ...`)
if (import.meta.url === `file://${process.argv[1]}`) {
  const a = process.argv.slice(2);
  const opt = k => (a.includes(k) ? a[a.indexOf(k) + 1] : null);
  try {
    if (a[0] === 'claim') console.log(JSON.stringify(claim({ holder: opt('--holder') || 'orbit-sp1', udid: opt('--udid') })));
    else if (a[0] === 'release' && a[1]) { release({ udid: a[1], lease: true, holder: opt('--holder') || 'orbit-sp1' }); console.log(JSON.stringify({ released: a[1] })); }
    else if (a[0] === 'mode') console.log(mode());
    else { console.error('usage: sim claim [--holder h] [--udid u] | sim release <udid> [--holder h] | sim mode'); process.exit(2); }
  } catch (e) { console.error(`sim: ${e.message}`); process.exit(1); }
}
