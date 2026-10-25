#!/usr/bin/env node
// perf-climb harness. Zero deps. Drives the argent CLI with no LLM in the loop, reads in-app marks,
// and ratchets a per-scenario baseline. Runtimes: "rn" reads globalThis.__PERF__ (rn-perf-climb
// assets/perf.ts) over the Hermes debugger; "native" reads Library/Caches/perf.jsonl from the
// simulator app container (native-perf-climb assets/Perf.swift). Run from the app's project root;
// state lives in ./perf/.
//
//   perf.mjs detect [dir]                                              rn | native (swiftui/uikit) | unsupported
//   perf.mjs bench <scenario|all> [--label L] [--reps N] [--warmup N] [--nobuild]  runs config.build, N reps, compares
//   perf.mjs bench <scenario> --quick                                   3 reps, no warmup: iterate fast (not acceptable)
//   perf.mjs bench <scenario> --target <metric>                         stop early (>=3 reps) once the verdict is decisive
//   perf.mjs leakcheck <App.app|bundle.js|dir>...                       fail if level-2 perf routes leaked into a build
//   perf.mjs devices <n>                                               clone + boot n simulators, reps run in parallel
//   perf.mjs profile <scenario> [--no-native] [--preroll ms] [--tail ms] react (rn) + native profile, writes reports
//   perf.mjs stability <scenario>                                      A/A run on HEAD -> per-metric noise floors
//   perf.mjs compare <base.json> <cand.json>                            stats table, exit 1 on regression
//   perf.mjs guard                                                      run the correctness guard
//   perf.mjs accept <run|scenario> --target <metric> [--note T] [--force]  guard + ratchet, commits perf/
//   perf.mjs reject <run|scenario> --note T [--keep]                   log, revert the move, commit
//   perf.mjs report [scenario]                                          first baseline vs current
//   perf.mjs selftest

import { spawn, spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const DIR = path.join(ROOT, 'perf');
const MIN_EFFECT = 0.05; // smaller median moves are noise even when the CI agrees
const MIN_REPS = 3; // fewest reps a sequential bench may stop at
const ACCEPT_REPS = 5; // fewest reps accept takes, unless the run stopped decisively on that target
const SELF = new URL(import.meta.url).pathname;
// A move may not touch the ruler: marks, flows, level-1 benches, level-2 routes, pinned fixtures.
const GUARDED = [/^perf\//, /^\.argent\/flows\//, /(^|\/)[Pp]erf(Routes|Fixture|UnitTests|Bench)?\.(swift|tsx?)$/, /\.perf\.test\.[jt]sx?$/];
const LEAK_MARKERS = ['PERF_ROUTES_ENABLED', 'PerfRoutes']; // must never reach a release build

// ---------- argent ----------

function argent(args, { allowFail = false } = {}) {
  const r = spawnSync('argent', args, { cwd: ROOT, encoding: 'utf8', maxBuffer: 256 << 20 });
  const out = r.stdout ?? '';
  const i = out.search(/^[{[]/m); // update notices are printed before the JSON
  let json = null;
  if (i >= 0) try { json = JSON.parse(out.slice(i)); } catch {}
  if (r.status !== 0 && !allowFail)
    throw new Error(`argent ${args.slice(0, 2).join(' ')} failed:\n${(r.stderr || out).slice(-1500)}`);
  return { ok: r.status === 0, json, out };
}

const portArgs = (c) => (c.port ? ['--port', String(c.port)] : []);

function evaluate(c, expression) {
  const { json } = argent(['run', 'debugger-evaluate', '--device_id', c.device, '--expression', expression, ...portArgs(c)]);
  return json?.result;
}

function simctl(...args) {
  const r = spawnSync('xcrun', ['simctl', ...args], { encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`simctl ${args[0]}: ${r.stderr.trim()}`);
  return r.stdout.trim();
}
const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

// How marks get out of the app. read() returns { epoch0, marks: [{n,t}], counts, dev }.
const RUNTIMES = {
  rn: {
    begin() {},
    reset: (c) => evaluate(c, 'globalThis.__PERF__?.reset(), 0'),
    read(c) {
      const raw = evaluate(c, 'JSON.stringify(globalThis.__PERF__ ?? null)');
      return typeof raw === 'string' ? JSON.parse(raw) : raw;
    },
    missing: 'globalThis.__PERF__ missing: is perf.ts imported first in the entry file?',
    route(c, r) {
      const ok = evaluate(c, `globalThis.__perfRoute ? (globalThis.__perfRoute(${JSON.stringify(r)}), 1) : 0`);
      return ok ? { ok: true } : { ok: false, reason: 'globalThis.__perfRoute missing: is PerfRouteHost mounted and Metro running with PERF_ROUTES=1?' };
    },
  },
  // Perf.swift appends JSONL events with host-epoch ms (the simulator shares the host clock), so
  // there is no reset: only events at or after t0 count.
  native: {
    begin(c) {
      if (!c.bundleId) throw new Error('"bundleId" is required in perf/perf.config.json for runtime native');
      (c.container ??= {})[c.device] = null; // the container moves on reinstall; resolve after build
      fs.rmSync(this.file(c), { force: true });
      this.end(c);
      simctl('spawn', c.device, 'defaults', 'write', c.bundleId, 'perf', '-bool', 'YES'); // enables the hitch monitor
    },
    reset() {},
    // Hash of the installed executable: lets accept catch "code changed, app didn't".
    stamp(c) {
      const app = simctl('get_app_container', c.device, c.bundleId, 'app');
      return crypto.createHash('sha1').update(fs.readFileSync(path.join(app, this.exe(c)))).digest('hex').slice(0, 12);
    },
    exe(c) {
      const app = simctl('get_app_container', c.device, c.bundleId, 'app');
      return JSON.parse(spawnSync('plutil', ['-convert', 'json', '-o', '-', path.join(app, 'Info.plist')], { encoding: 'utf8' }).stdout).CFBundleExecutable;
    },
    file: (c) => path.join(((c.container ??= {})[c.device] ??= simctl('get_app_container', c.device, c.bundleId, 'data')), 'Library/Caches/perf.jsonl'),
    read(c, t0) {
      // Perf.swift flushes every 200ms: the file is complete once it has gone quiet for longer than that.
      const f = this.file(c);
      for (const end = Date.now() + 1500; Date.now() < end; sleep(50))
        if (fs.existsSync(f) && Date.now() - fs.statSync(f).mtimeMs > 250) break;
      return fs.existsSync(f) ? parseEvents(fs.readFileSync(f, 'utf8'), t0) : null;
    },
    missing: 'no perf.jsonl in the app container: is Perf.start() called at launch, and is bundleId right?',
    // A leftover route would mount over the next relaunch (a level-3 setup, the guard's QA flow).
    end: (c) => fs.rmSync(path.join(path.dirname(RUNTIMES.native.file(c)), 'perf-route.txt'), { force: true }),
    // Level 2 without deep links (iOS asks "Open in …?" on every external custom-scheme open): write
    // the route into the container; PerfRoutes.watch() picks it up. Launch is a no-op when running.
    route(c, r) {
      spawnSync('xcrun', ['simctl', 'launch', c.device, c.bundleId]);
      const dir = path.dirname(this.file(c));
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'perf-route.txt'), `${r}\n${Date.now()}\n`);
      return { ok: true };
    },
  },
};

function runFlow(c, ref) {
  const r = argent(['flow', 'run', ref, '--device', c.device, '--json'], { allowFail: true });
  const bad = r.json?.steps?.find((s) => s.status !== 'pass' && s.status !== 'skip');
  return { ok: r.ok && r.json?.ok !== false, reason: bad ? `step ${bad.index} ${bad.kind}: ${bad.reason ?? bad.status}` : r.out.slice(-500) };
}

// A flow whose first real step is `launch:` restarts the app, which kills a profiler session.
function relaunches(ref) {
  const file = ref.endsWith('.yaml') ? path.resolve(ROOT, ref) : path.join(ROOT, '.argent/flows', `${ref}.yaml`);
  const steps = [...fs.readFileSync(file, 'utf8').matchAll(/^\s*-\s*([\w-]+):/gm)].map((m) => m[1]);
  return steps.find((s) => s !== 'echo' && s !== 'script') === 'launch';
}

function pickDevice(platform) {
  const { json } = argent(['run', 'list-devices']);
  const up = json.devices.filter((d) => ['Booted', 'device', 'Running'].includes(d.state) && (!platform || d.platform === platform));
  if (up.length !== 1) throw new Error(`need exactly one booted ${platform ?? ''} device, found ${up.length}; set "device" in perf/perf.config.json`);
  return up[0].udid ?? up[0].serial ?? up[0].id;
}

function config({ device = true } = {}) {
  const c = JSON.parse(fs.readFileSync(path.join(DIR, 'perf.config.json'), 'utf8'));
  c.runtime ??= detect(ROOT).runtime;
  if (!RUNTIMES[c.runtime]) throw new Error(`runtime "${c.runtime}" not supported; set "runtime": "rn" | "native"`);
  c.rt = RUNTIMES[c.runtime];
  if (device) {
    c.devices ??= [c.device ?? pickDevice(c.platform)];
    c.device ??= c.devices[0];
  }
  return c;
}

// Perf.swift JSONL -> the shape rep() expects. Only events at or after t0 belong to this rep.
export function parseEvents(text, t0) {
  const p = { epoch0: 0, marks: [], counts: {}, dev: null, raw: [] };
  for (const line of text.split('\n')) {
    if (!line) continue;
    const e = JSON.parse(line);
    if (e.k === 'meta') p.dev = e.debug;
    if (e.t < t0 && e.k !== 'meta') continue;
    p.raw.push(line); // this rep's trace, for `orbit perf` (journey -> screen -> phase attribution)
    if (e.k === 'meta') continue;
    if (e.k === 'm') p.marks.push({ n: e.n, t: e.t });
    else if (e.k === 'c') p.counts[e.n] = (p.counts[e.n] ?? 0) + e.v;
    else if (e.k === 'f') p.counts[`fixture:${e.n}`] = e.v;
  }
  p.marks.sort((a, b) => a.t - b.t);
  return p;
}

// ---------- detect ----------

// Deterministic project classification. RN wins over the ios/ Xcode project it contains.
export function detect(root) {
  const SKIP = new Set(['node_modules', 'Pods', 'build', 'DerivedData', '.build', '.git', 'vendor', 'Carthage', '.expo']);
  const found = { pkg: [], xcode: [], swift: [], flutter: [], gradle: [] };
  const walk = (dir, depth) => {
    let ents;
    try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of ents) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name.endsWith('.xcodeproj')) found.xcode.push(p);
        else if (!SKIP.has(e.name) && !e.name.startsWith('.')) walk(p, depth + 1);
      } else if (e.name === 'package.json' && depth <= 3) found.pkg.push(p);
      else if (e.name === 'Package.swift' && depth <= 3) found.xcode.push(p);
      else if (e.name === 'pubspec.yaml' && depth <= 3) found.flutter.push(p);
      else if (/^build\.gradle(\.kts)?$/.test(e.name) && depth <= 3) found.gradle.push(p);
      else if (e.name.endsWith('.swift')) found.swift.push(p);
    }
  };
  walk(path.resolve(root), 0);
  const rel = (p) => path.relative(root, p) || '.';
  for (const p of found.pkg) {
    try {
      const j = JSON.parse(fs.readFileSync(p, 'utf8'));
      const deps = { ...j.dependencies, ...j.devDependencies };
      if (deps['react-native'] || deps.expo)
        return { runtime: 'rn', skill: 'rn-perf-climb', root: rel(path.dirname(p)), evidence: `${rel(p)}: react-native ${deps['react-native'] ?? `(expo ${deps.expo})`}` };
    } catch {}
  }
  if (found.flutter.length) return { runtime: 'unsupported', why: `Flutter (${rel(found.flutter[0])})` };
  if (found.xcode.length) {
    let swiftui = 0, uikit = 0;
    for (const f of found.swift) {
      const head = fs.readFileSync(f, 'utf8').slice(0, 4000);
      if (/^import SwiftUI\b/m.test(head)) swiftui++;
      if (/^import UIKit\b/m.test(head)) uikit++;
    }
    const flavour = swiftui >= 2 * uikit ? 'swiftui' : uikit >= 2 * swiftui ? 'uikit' : 'mixed';
    return { runtime: 'native', skill: 'native-perf-climb', flavour, root: rel(path.dirname(found.xcode[0])),
      evidence: `${rel(found.xcode[0])}; ${swiftui} SwiftUI / ${uikit} UIKit files` };
  }
  if (found.gradle.length) return { runtime: 'unsupported', why: `native Android (${rel(found.gradle[0])}); argent native-profiler supports it but no Android climb skill exists yet` };
  return { runtime: 'unsupported', why: 'no React Native package.json, Xcode project or Package.swift found' };
}

const gitx = (...a) => spawnSync('git', a, { cwd: ROOT, encoding: 'utf8' });

function git() {
  const sha = gitx('rev-parse', '--short', 'HEAD');
  if (sha.status !== 0) return null;
  return { sha: sha.stdout.trim(), dirty: gitx('status', '--porcelain').stdout.trim() !== '' };
}

// The harness owns perf/ commits: pathspec-only, so unrelated staged work is never swept in.
// The run measured the code at HEAD if everything since is harness bookkeeping (perf/ only).
function sameCode(sha, head) {
  if (sha === head) return true;
  const files = gitx('diff', '--name-only', sha, head).stdout.split('\n').filter(Boolean);
  return gitx('merge-base', '--is-ancestor', sha, head).status === 0 && files.every((f) => f.startsWith('perf/'));
}

function commit(msg, files) {
  if (!git()) return console.error('warn: not a git repo, nothing committed');
  const rel = files.map((f) => path.relative(ROOT, f));
  gitx('add', '--', ...rel);
  const r = gitx('commit', '-q', '-m', msg, '--', ...rel);
  if (r.status !== 0) throw new Error(`git commit failed: ${(r.stderr || r.stdout).trim()}`);
  console.log(`committed ${git().sha} ${msg}`);
}

// Correctness gate: a faster app that broke behaviour is not a win. Must pass at baseline and at
// every accepted move. Not overridable by --force.
function guard(c) {
  if (!c.guard) return { ok: true, status: 'none' };
  console.error(`guard: ${c.guard}`);
  const r = spawnSync(c.guard, { cwd: ROOT, shell: true, encoding: 'utf8', maxBuffer: 256 << 20 });
  return { ok: r.status === 0, status: r.status === 0 ? 'pass' : 'fail', tail: (r.stdout + r.stderr).trim().split('\n').slice(-15).join('\n') };
}

// ---------- measurement ----------

// Pair each start mark with the next end mark. 2nd occurrence of a span becomes "<name>#2".
export function spans(perf, defs, t0) {
  const out = {};
  const at = (n) => (n === '@launch' ? [t0 - perf.epoch0] : perf.marks.filter((m) => m.n === n).map((m) => m.t));
  for (const [name, [a, b]] of Object.entries(defs)) {
    const ends = at(b);
    let j = 0, k = 0;
    for (const s of at(a)) {
      while (j < ends.length && ends[j] < s) j++;
      if (j === ends.length) break;
      out[k ? `${name}#${k + 1}` : name] = Math.round((ends[j++] - s) * 10) / 10;
      k++;
    }
  }
  return out;
}

// "until": wait for the app's own end mark instead of a UI-tree gate. A slow screen can be too
// busy (or too big) for the accessibility tree to answer, while the mark still arrives.
function waitUntil(c, s, t0) {
  let perf = c.rt.read(c, t0);
  // A deep link may cold-launch the app: until the event file exists, keep polling too.
  for (const deadline = Date.now() + (s.untilMs ?? 30000); s.until && !perf?.marks.some((m) => m.n === s.until); ) {
    if (Date.now() > deadline) return { error: `until: no "${s.until}" mark within ${s.untilMs ?? 30000}ms` };
    sleep(200);
    perf = c.rt.read(c, t0);
  }
  // Tail: events that land after the end mark (a busy stretch ending, the quiet window that makes
  // `settled` in `orbit perf`). Not part of any span, so metrics don't move; only the trace grows.
  const tail = s.tailMs ?? 500;
  if (s.until && tail > 0) { sleep(tail); perf = c.rt.read(c, t0) ?? perf; }
  return { perf };
}

// "{device}" in the command runs it once per device (xcodebuild is incremental after the first).
function build(c, o) {
  if (!c.build || o.nobuild) return;
  for (const d of c.build.includes('{device}') ? c.devices : [null]) {
    const cmd = d ? c.build.replaceAll('{device}', d) : c.build;
    console.error(`build: ${cmd}`);
    const b = spawnSync(cmd, { cwd: ROOT, shell: true, encoding: 'utf8', maxBuffer: 256 << 20 });
    if (b.status !== 0) throw new Error(`build failed (exit ${b.status}), not measuring a stale app:\n${(b.stdout + b.stderr).slice(-2000)}`);
  }
}

// One rep in a worker process, so several simulators can run concurrently with the sync code above.
function repOn(name, device) {
  return new Promise((res) => {
    const p = spawn(process.execPath, [SELF, '_rep', name, '--device', device], { cwd: ROOT });
    let out = '', err = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (err += d));
    p.on('close', () => {
      try { res(JSON.parse(out.trim().split('\n').pop())); } catch { res({ error: `worker on ${device} died: ${(err || out).slice(-400)}` }); }
    });
  });
}

// Sequential stopping: the target's verdict can no longer change, and no guardrail is drifting the
// wrong way undecided. ponytail: heuristic for guardrails (median <= base counts as safe); a guardrail
// that only regresses in the tail can slip through an early stop. Full reps if that matters.
export function decisive(rows, target) {
  for (const r of rows) {
    if (r.verdict === 'gone' || r.verdict === 'MISMATCH' || r.verdict === 'regressed') return true; // will fail anyway
    if (r.lo === undefined || r.metric.startsWith('fixture:')) continue;
    const f = r.floor;
    if (r.metric === target) { if (!(r.hi < -f || r.lo > f || (r.lo > -f && r.hi < f))) return false; }
    else if (!(r.hi < f || r.lo > f || r.d <= 0)) return false;
  }
  return rows.some((r) => r.metric === target);
}

// Level 2 scenarios name a "route" instead of a flow: the app mounts that subsystem directly.
const act = (c, s) => (s.route ? c.rt.route(c, s.route) : runFlow(c, s.flow));

function rep(c, s, { react = false } = {}) {
  if (s.setup) {
    const f = runFlow(c, s.setup);
    if (!f.ok) return { error: `setup: ${f.reason}` };
  } else if (s.route) {
    const b = c.rt.route(c, 'blank');
    if (!b.ok) return { error: `route blank: ${b.reason}` };
    sleep(150); // let the blank route land before the reset
  }
  c.rt.reset(c);
  if (react) argent(['run', 'react-profiler-start', '--device_id', c.device, ...portArgs(c)]);
  const t0 = Date.now();
  const f = act(c, s);
  const flowMs = Date.now() - t0;
  const stop = react ? argent(['run', 'react-profiler-stop', '--device_id', c.device, ...portArgs(c)]).json : null;
  if (!f.ok) return { error: `flow: ${f.reason}` };

  const { perf, error } = waitUntil(c, s, t0);
  if (error) return { error };
  if (!perf) return { error: c.rt.missing };

  const m = { ...(!s.route && { flow_ms: flowMs }), ...spans(perf, s.spans ?? {}, t0) };
  const missing = Object.keys(s.spans ?? {}).filter((k) => !(k in m));
  if (missing.length) return { error: `no span ${missing.join(', ')}: marks seen ${[...new Set(perf.marks.map((x) => x.n))].join(', ')}` };
  for (const [k, v] of Object.entries(perf.counts)) m[k.startsWith('fixture:') ? k : `count:${k}`] = v;
  if (stop) {
    m['react:commits'] = stop.total_react_commits;
    m['react:fiber_renders'] = stop.fiber_renders_captured;
    m['react:hot_commits'] = stop.hot_commit_indices?.length ?? 0;
    delete m.flow_ms; // profiler overhead; timings come from the plain reps
  }
  // Native shares the host clock: stamp when the harness stopped listening, so an idle tail (no events)
  // still proves quiet time for `settled`.
  if (perf.raw && c.runtime === 'native') perf.raw.push(JSON.stringify({ k: 'm', n: 'trace:end', t: Date.now() }));
  return { m, dev: perf.dev, trace: perf.raw };
}

async function bench(name, o) {
  const c = config();
  if (name === 'all') {
    let worst = 0;
    for (const n of Object.keys(c.scenarios)) worst = Math.max(worst, await bench(n, { ...o, target: undefined }));
    return worst;
  }
  const { run, file } = await measure(c, name, o);
  const base = baseline(name);
  if (!base) {
    table(Object.entries(run.samples).map(([k, v]) => ({ metric: k, base: '', cand: med(v), delta: '', ci: `±${mad(v)}`, verdict: '' })));
    console.log(`no baseline yet: perf.mjs accept ${path.relative(ROOT, file)} --target <metric>`);
    return 0;
  }
  let rows;
  try { rows = compare(base, run, floors(name)); } catch (e) {
    console.log(`${e.message}\nThis run is a new ruler: perf.mjs accept ${path.relative(ROOT, file)} --force --note "re-baseline: <why>"`);
    return 1;
  }
  table(rows);
  return rows.some(blocking) ? 1 : 0;
}

// A/A: the same code measured twice. Whatever moves is noise this setup can't tell apart from a fix,
// so it becomes that metric's minimum effect. Lives under perf/ (the ruler): after the baseline,
// changing it needs a forced re-baseline, so floors can't be inflated to hide a regression.
async function stability(name, o) {
  const c = config();
  if (git()?.dirty) throw new Error('commit first: stability measures HEAD');
  const a = (await measure(c, name, { ...o, label: 'aa-1', target: undefined, quick: false })).run;
  const b = (await measure(c, name, { ...o, label: 'aa-2', target: undefined, quick: false, nobuild: true })).run;
  const rows = compare(a, b);
  const fl = {};
  for (const r of rows) {
    if (r.d === undefined || r.metric.startsWith('fixture:')) continue;
    // drift between sessions (2x the A/A median move) or sampling noise (A/A CI half-width), whichever is larger
    fl[r.metric] = Math.max(MIN_EFFECT, Math.ceil(Math.max(Math.abs(r.d) * 2, (r.hi - r.lo) / 2) * 100) / 100);
    r.verdict = r.verdict === 'noise' ? `floor ${pct(fl[r.metric])}` : `UNSTABLE: same code reads ${r.verdict}, floor ${pct(fl[r.metric])}`;
  }
  table(rows);
  const f = stabilityFile(name);
  write(f, { scenario: name, at: new Date().toISOString(), git: git(), floors: fl });
  commit(`perf: stability ${name}`, [f]);
  const bad = rows.filter((r) => r.verdict.startsWith('UNSTABLE'));
  if (bad.length) console.log(`\n${bad.length} metric(s) moved with no code change: pin data, kill background apps, or treat them as guardrails only`);
  return 0;
}

// Level 1: a command that mounts the subsystem itself (Jest + Profiler, hosted XCTest) and prints
// "METRIC <name>=<n>" per rep, plus "FIXTURE <name>=<n>" and "META debug=true|false". One process for
// all reps (PERF_REPS / PERF_WARMUP, also as TEST_RUNNER_* for xcodebuild), since tool startup dominates.
export function parseMetrics(text) {
  const samples = {};
  let dev = null;
  for (const line of text.split('\n')) {
    const m = line.match(/\b(METRIC|FIXTURE|META) (.+?)=(-?[\d.]+(?:e[-+]?\d+)?|true|false)\s*$/);
    if (!m) continue;
    if (m[1] === 'META') { if (m[2] === 'debug') dev = m[3] === 'true'; continue; }
    (samples[m[1] === 'FIXTURE' ? `fixture:${m[2]}` : m[2]] ??= []).push(+m[3]);
  }
  return { samples, dev };
}

function measureUnit(c, name, s, o) {
  const reps = +(o.reps ?? (o.quick ? MIN_REPS : s.reps ?? 7)), warmup = +(o.warmup ?? (o.quick ? 0 : s.warmup ?? 1));
  if (s.prepare && !o.nobuild) {
    console.error(`prepare: ${s.prepare}`);
    const b = spawnSync(s.prepare, { cwd: ROOT, shell: true, encoding: 'utf8', maxBuffer: 256 << 20 });
    if (b.status !== 0) throw new Error(`prepare failed (exit ${b.status}):\n${(b.stdout + b.stderr).slice(-2000)}`);
  }
  console.error(`bench ${name} (level 1): ${warmup} warmup + ${reps} reps in one process`);
  const env = { ...process.env, PERF_REPS: String(reps), PERF_WARMUP: String(warmup), TEST_RUNNER_PERF_REPS: String(reps), TEST_RUNNER_PERF_WARMUP: String(warmup) };
  const r = spawnSync(s.cmd, { cwd: ROOT, shell: true, encoding: 'utf8', maxBuffer: 256 << 20, env });
  const { samples, dev } = parseMetrics(`${r.stdout}\n${r.stderr}`);
  if (r.status !== 0 || !Object.keys(samples).length)
    throw new Error(`level-1 cmd ${r.status !== 0 ? `exited ${r.status}` : 'printed no METRIC lines'}:\n${(r.stdout + r.stderr).slice(-2000)}`);
  const run = {
    scenario: name, label: o.label ?? 'run', at: new Date().toISOString(), git: git(), tier: 'unit',
    device: null, devices: [], parallel: 1, platform: c.platform, dev, reps: Math.min(...Object.values(samples).map((v) => v.length)),
    warmup, quick: !!o.quick, spans: s.spans ?? {}, failures: [], samples,
  };
  const file = path.join(DIR, 'runs', name, `${run.at.replace(/[:.]/g, '-')}-${run.label.replace(/[^\w-]/g, '_')}.json`);
  write(file, run);
  console.log(`run ${path.relative(ROOT, file)}`);
  return { run, file };
}

async function measure(c, name, o) {
  const s = c.scenarios[name];
  if (!s) throw new Error(`no scenario "${name}" in perf/perf.config.json`);
  if (s.cmd) return measureUnit(c, name, s, o);
  const quick = !!o.quick;
  const maxReps = +(o.reps ?? (quick ? MIN_REPS : s.reps ?? 7)), warmup = +(o.warmup ?? (quick ? 0 : s.warmup ?? 1));
  const devs = c.devices;
  const samples = {}, failures = [], traces = [];
  let dev = null, n = 0, stopped = false;
  const collect = (r, i) => {
    if (r.error) {
      failures.push({ rep: i, error: r.error });
      console.error(`  rep ${i} FAIL ${r.error}`);
      if (failures.length === 2 && !Object.keys(samples).length) throw new Error('first reps failed, fix the flow before measuring');
      return;
    }
    dev ??= r.dev;
    if (i < 0) return;
    for (const [k, v] of Object.entries(r.m)) (samples[k] ??= []).push(v);
    if (r.trace && !('react:commits' in r.m)) traces.push(r.trace);
  };

  // Without a relaunch per rep, state leaks between reps and a stale bundle can measure old code
  // (every fix then reads as noise). debugger-reload-metro is not a substitute: it breaks Expo Go.
  if (!s.route && !(s.setup ? relaunches(s.setup) : relaunches(s.flow))) console.error('warn: no launch: at the start of setup/flow, reps share app state and may run a stale bundle');
  if (s.react && c.runtime !== 'rn') throw new Error(`${name}: "react": true is RN-only. Native work counters come from Perf.count in the app`);
  build(c, o);
  for (const d of devs) { c.device = d; c.rt.begin(c); }
  c.device = devs[0];
  const stamps = [...new Set(devs.map((d) => c.rt.stamp?.({ ...c, device: d })))];
  if (stamps.length > 1) throw new Error(`devices run different app builds (${stamps.join(', ')}): use "{device}" in "build"`);

  const base = baseline(name), fl = floors(name);
  let sequential = !!o.target && !!base && !quick;
  const one = devs.length > 1 ? (d) => repOn(name, d) : async () => rep(c, s);
  console.error(`bench ${name} (${c.runtime}): ${warmup} warmup + ${sequential ? `${MIN_REPS}..` : ''}${maxReps} reps on ${devs.length} device(s)${quick ? ' [quick]' : ''}`);
  for (let w = 0; w < warmup; w++) (await Promise.all(devs.map(one))).forEach((r) => collect(r, -1));
  while (n < maxReps) {
    const batch = await Promise.all(devs.slice(0, maxReps - n).map(one));
    for (const r of batch) collect(r, n++);
    if (sequential && n >= MIN_REPS && n < maxReps) {
      const partial = { dev, platform: c.platform, device: c.device, parallel: devs.length, spans: s.spans ?? {}, samples };
      let rows;
      try { rows = compare(base, partial, fl); } catch (e) { console.error(`  no early stop: ${e.message}`); sequential = false; continue; }
      if (decisive(rows, o.target)) { stopped = true; console.error(`  decisive on ${o.target} after ${n} reps`); break; }
    }
  }
  for (const d of devs) c.rt.end?.({ ...c, device: d });
  if (s.react) {
    if (s.flow && relaunches(s.flow)) throw new Error(`${name}: "react": true needs a flow that does not start with launch (put launch in "setup")`);
    for (let i = 0; i < (s.workReps ?? 3); i++) collect(rep(c, s, { react: true }), i);
  }
  if (failures.length > n / 2) throw new Error(`${failures.length} failed reps, fix the flow before measuring`);

  const run = {
    scenario: name, label: o.label ?? 'run', at: new Date().toISOString(), git: git(),
    device: c.device, devices: devs, parallel: devs.length, platform: c.platform, build: stamps[0], dev,
    tier: s.tier ?? 'app', reps: n, warmup, quick, target: o.target, decisive: stopped, spans: s.spans ?? {}, failures, samples,
  };
  const file = path.join(DIR, 'runs', name, `${run.at.replace(/[:.]/g, '-')}-${run.label.replace(/[^\w-]/g, '_')}.json`);
  write(file, run);
  // One JSONL per rep, harness meta first (it wins over the app's): `orbit perf <dir>` compares arms by impl.
  const meta = JSON.stringify({ k: 'meta', impl: s.impl ?? c.impl ?? 'app', scenario: name, journey: s.journey ?? name, platform: c.platform,
    device: c.device, parallel: devs.length, debug: dev, commit: run.git?.sha ?? null, build: stamps[0] ?? null });
  traces.forEach((t, i) => {
    const f = path.join(file.replace(/\.json$/, ''), `rep-${i + 1}.jsonl`);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, [meta, ...t].join('\n') + '\n');
  });
  console.log(`run ${path.relative(ROOT, file)}${traces.length ? ` (+${traces.length} traces)` : ''}`);
  return { run, file };
}

// Level-2 routes are test-only. Scan built artifacts (an .app, a JS bundle, a dir) for their markers.
function leakcheck(paths) {
  if (!paths.length) throw new Error('usage: perf.mjs leakcheck <App.app|bundle.js|dir>...');
  const hits = [];
  const scan = (p) => {
    const st = fs.statSync(p);
    if (st.isDirectory()) return fs.readdirSync(p).forEach((f) => scan(path.join(p, f)));
    if (st.size > 512 << 20) return;
    const buf = fs.readFileSync(p);
    for (const m of LEAK_MARKERS) if (buf.includes(m)) hits.push(`${p}: ${m}`);
  };
  paths.forEach((p) => scan(path.resolve(ROOT, p)));
  console.log(hits.length ? `LEAK: perf routes in a build\n${hits.join('\n')}` : `clean: no ${LEAK_MARKERS.join('/')} in ${paths.join(' ')}`);
  return hits.length ? 1 : 0;
}

// Clone the primary simulator into n identical devices (installed apps and data come along) and boot
// them. Writes "devices" into the config: parallelism is part of the ruler, so re-baseline after.
function devices(n) {
  if (!(n >= 1)) throw new Error('usage: perf.mjs devices <n>');
  const cfg = path.join(DIR, 'perf.config.json');
  const raw = read(cfg);
  const src = raw.device ?? raw.devices?.[0] ?? pickDevice(raw.platform);
  const all = Object.values(JSON.parse(simctl('list', 'devices', '-j')).devices).flat();
  const srcName = all.find((d) => d.udid === src)?.name ?? 'sim';
  const list = [src];
  for (let i = 1; i < n; i++) {
    const name = `${srcName} perf-${i}`;
    let udid = all.find((d) => d.name === name)?.udid;
    if (!udid) {
      spawnSync('xcrun', ['simctl', 'shutdown', src]); // clone needs a shut-down source
      udid = simctl('clone', src, name);
      console.error(`cloned ${name} ${udid}`);
    }
    list.push(udid);
  }
  for (const u of list) argent(['run', 'boot-device', '--udid', u]);
  write(cfg, { ...raw, device: src, devices: list });
  console.log(`devices: ${list.join(' ')}\nconfig updated. Parallelism is part of the ruler: run stability, then re-baseline (accept --force --note).`);
  return 0;
}

// ---------- profile (discovery, not a metric) ----------

function profile(name, o) {
  const c = config(), s = c.scenarios[name];
  if (s.cmd) throw new Error(`${name}: level-1 scenarios run outside the app; profile a level 2/3 scenario`);
  if (s.flow && relaunches(s.flow)) throw new Error(`${name}: profiling needs a flow that does not start with launch (put launch in "setup"). For startup, use marks + "native" with the app already open.`);
  const react = c.runtime === 'rn';
  const native = o.native !== false || !react;
  build(c, o);
  c.rt.begin(c);
  if (s.setup && !runFlow(c, s.setup).ok) throw new Error('setup flow failed');
  if (react) argent(['run', 'react-profiler-start', '--device_id', c.device, ...portArgs(c)]);
  // With several apps running the profiler can't guess; native knows its executable, RN takes appProcess.
  const proc = c.appProcess ?? c.rt.exe?.(c);
  if (native) {
    argent(['run', 'native-profiler-start', '--device_id', c.device, ...(proc ? ['--app_process', proc] : [])]);
    sleep(+(o.preroll ?? 3000)); // xctrace returns before it is recording; without this the flow is missed
  }
  if (s.route) c.rt.route(c, 'blank');
  const t0 = Date.now();
  const f = act(c, s);
  const w = f.ok ? waitUntil(c, s, t0) : {};
  sleep(+(o.tail ?? 1000)); // let trailing work (layout, decode, late renders) land in the trace
  console.error(`flow ${f.ok ? 'ok' : 'FAIL ' + f.reason}${w.error ? ` (${w.error})` : ''} in ${Date.now() - t0}ms`);
  if (react) console.log(JSON.stringify(argent(['run', 'react-profiler-stop', '--device_id', c.device, ...portArgs(c)]).json));
  if (native) argent(['run', 'native-profiler-stop', '--device_id', c.device]);

  const out = path.join(DIR, 'profiles', `${name}-${new Date().toISOString().replace(/[:.]/g, '-')}`);
  fs.mkdirSync(out, { recursive: true });
  let rn;
  try { rn = JSON.parse(fs.readFileSync(path.join(ROOT, 'node_modules/react-native/package.json'), 'utf8')).version; } catch {}
  const reports = {
    ...(react && { react: ['react-profiler-analyze', '--device_id', c.device, '--project_root', ROOT, '--platform', c.platform, ...(rn ? ['--rn_version', rn] : []), ...portArgs(c)] }),
    ...(native && { native: ['native-profiler-analyze', '--device_id', c.device] }),
    ...(react && native && { combined: ['profiler-combined-report', '--device_id', c.device, ...portArgs(c)] }),
  };
  for (const [k, args] of Object.entries(reports)) {
    const r = argent(['run', ...args], { allowFail: true });
    fs.writeFileSync(path.join(out, `${k}.md`), r.json?.report ?? r.json?.markdown ?? r.out);
    console.log(`${k} ${path.relative(ROOT, out)}/${k}.md${r.ok ? '' : ' (FAILED, see file)'}`);
  }
}

// ---------- stats ----------

export const med = (a) => {
  const s = [...a].sort((x, y) => x - y), n = s.length;
  return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : NaN;
};
const mad = (a) => Math.round(med(a.map((x) => Math.abs(x - med(a)))) * 10) / 10;

function rng(seed) { // mulberry32: same inputs, same CI, every time
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// 90% bootstrap CI of the relative change in medians.
function ci(a, b, iters = 2000) {
  const r = rng(42), pick = (x) => Array.from(x, () => x[(r() * x.length) | 0]);
  const d = [];
  for (let i = 0; i < iters; i++) {
    const ma = med(pick(a)), mb = med(pick(b));
    d.push(ma === 0 ? (mb === 0 ? 0 : Infinity) : (mb - ma) / ma);
  }
  d.sort((x, y) => x - y);
  return [d[(iters * 0.05) | 0], d[(iters * 0.95) | 0]];
}

// floors: per-metric minimum effect from `stability`; MIN_EFFECT when absent.
export function compare(A, B, floors = {}) {
  if (A.dev !== B.dev) throw new Error(`refusing: __DEV__ ${A.dev} vs ${B.dev}. Dev and release timings are not comparable.`);
  if (A.platform !== B.platform) throw new Error(`refusing: platform ${A.platform} vs ${B.platform}`);
  if ((A.parallel ?? 1) !== (B.parallel ?? 1)) throw new Error(`refusing: ${A.parallel ?? 1} vs ${B.parallel ?? 1} parallel simulators. Host contention shifts timings; re-baseline at the new parallelism`);
  if (JSON.stringify(A.spans) !== JSON.stringify(B.spans)) throw new Error('refusing: span definitions changed, re-baseline with accept --force --note');
  if (A.device !== B.device) console.error(`warn: device ${A.device} vs ${B.device}`);
  const rows = [];
  for (const k of [...new Set([...Object.keys(A.samples), ...Object.keys(B.samples)])]) {
    const a = A.samples[k], b = B.samples[k];
    if (!a || !b) { rows.push({ metric: k, base: a ? med(a) : '', cand: b ? med(b) : '', delta: '', ci: '', verdict: a ? 'gone' : 'new' }); continue; }
    const ma = med(a), mb = med(b);
    const delta = ma === 0 ? (mb === 0 ? 0 : Infinity) : (mb - ma) / ma;
    const [lo, hi] = ci(a, b);
    const floor = floors[k] ?? MIN_EFFECT;
    let verdict = 'noise';
    if (k.startsWith('fixture:')) verdict = ma === mb ? 'same' : 'MISMATCH';
    else if (hi < 0 && -delta >= floor) verdict = 'improved';
    else if (lo > 0 && delta >= floor) verdict = 'regressed';
    rows.push({ metric: k, base: r1(ma), cand: r1(mb), delta: pct(delta), ci: `${pct(lo)}..${pct(hi)}`, verdict, d: delta, lo, hi, floor });
  }
  return rows;
}

const r1 = (x) => Math.round(x * 10) / 10;
const blocking = (r) => ['regressed', 'MISMATCH', 'gone'].includes(r.verdict);
const pct = (x) => (Number.isFinite(x) ? `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%` : 'inf');

function table(rows) {
  const cols = ['metric', 'base', 'cand', 'delta', 'ci', 'verdict'];
  const w = cols.map((c) => Math.max(c.length, ...rows.map((r) => String(r[c]).length)));
  for (const r of [Object.fromEntries(cols.map((c) => [c, c])), ...rows]) console.log(cols.map((c, i) => String(r[c]).padEnd(w[i])).join('  '));
}

// ---------- ratchet ----------

const read = (f) => JSON.parse(fs.readFileSync(f, 'utf8'));
// accept/reject take a run file or a scenario name (= its latest run)
function runFile(arg) {
  if (fs.existsSync(path.resolve(ROOT, arg))) return path.resolve(ROOT, arg);
  const dir = path.join(DIR, 'runs', arg);
  const last = fs.existsSync(dir) && fs.readdirSync(dir).filter((f) => f.endsWith('.json')).sort().pop();
  if (!last) throw new Error(`no run file or scenario runs for "${arg}"`);
  return path.join(dir, last);
}
function write(f, o) { fs.mkdirSync(path.dirname(f), { recursive: true }); fs.writeFileSync(f, JSON.stringify(o, null, 2) + '\n'); }
const baseFile = (s, init = false) => path.join(DIR, 'baseline', `${s}${init ? '.init' : ''}.json`);
const epochFile = (s) => path.join(DIR, 'baseline', `${s}.epoch.json`); // first baseline of the current ruler
const baseline = (s) => (fs.existsSync(baseFile(s)) ? read(baseFile(s)) : null);
const LEDGER = path.join(DIR, 'ledger.jsonl');
const ledger = (e) => { fs.mkdirSync(DIR, { recursive: true }); fs.appendFileSync(LEDGER, JSON.stringify({ at: new Date().toISOString(), ...e }) + '\n'); };
const ledgerEntries = () => (fs.existsSync(LEDGER) ? fs.readFileSync(LEDGER, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []);
const stabilityFile = (s) => path.join(DIR, 'stability', `${s}.json`);
const floors = (s) => (fs.existsSync(stabilityFile(s)) ? read(stabilityFile(s)).floors : {});

// Moving a mark or editing a flow changes the ruler, not the app. Such a move can't be accepted.
function rulerEdits(sinceSha) {
  if (!sinceSha) return [];
  const sh = (...a) => spawnSync('git', a, { cwd: ROOT, encoding: 'utf8' }).stdout ?? '';
  const files = sh('diff', '--name-only', sinceSha, '--', '.').split('\n').filter(Boolean)
    .concat(sh('ls-files', '--others', '--exclude-standard').split('\n').filter(Boolean));
  const hits = files.filter((f) => GUARDED.some((re) => re.test(f)) && !/^perf\/(runs|baseline|profiles|ledger|traces)/.test(f));
  const markLines = sh('diff', '-U0', sinceSha, '--', '.', ':!perf').split('\n')
    .filter((l) => /^[+-][^+-]/.test(l) && /\b(mark|count|fixture|span|milestone|firstFrame|Perf\.begin)\(\s*['"`]/.test(l));
  return [...hits, ...(markLines.length ? [`${markLines.length} changed mark()/count() lines`] : [])];
}

// Evidence per accepted commit: the run's traces, zstd-compressed when the CLI exists (orbit reads .jsonl.zst).
function keepTraces(file, run) {
  const src = file.replace(/\.json$/, '');
  if (!fs.existsSync(src)) return [];
  const dst = path.join(DIR, 'traces', run.scenario, `${run.git?.sha ?? 'nogit'}-${run.label.replace(/[^\w-]/g, '_')}`);
  fs.mkdirSync(dst, { recursive: true });
  const zstd = spawnSync('zstd', ['--version']).status === 0;
  for (const f of fs.readdirSync(src).filter((f) => f.endsWith('.jsonl'))) {
    if (zstd) spawnSync('zstd', ['-19', '-q', '-f', path.join(src, f), '-o', path.join(dst, `${f}.zst`)]);
    else fs.copyFileSync(path.join(src, f), path.join(dst, f));
  }
  return [dst];
}

function accept(file, o) {
  const c = config({ device: false });
  const run = read(runFile(file));
  const base = baseline(run.scenario);
  const head = git()?.sha;
  // Level 1 runs never count as wins: Jest is V8 not Hermes, XCTest is not the app. They can only move
  // their own reference point, so later level-1 benches compare against the current code.
  if (run.tier === 'unit') {
    if (run.git && head && !sameCode(run.git.sha, head)) throw new Error(`run measured ${run.git.sha} but HEAD is ${head}: bench again`);
    if (run.git?.dirty || run.quick) throw new Error('level-1 reference needs a clean tree and a full (non --quick) run');
    write(baseFile(run.scenario), run);
    if (!fs.existsSync(baseFile(run.scenario, true))) write(baseFile(run.scenario, true), run);
    ledger({ kind: 'unit-ref', scenario: run.scenario, label: run.label, git: run.git, note: o.note });
    commit(`perf: level-1 reference ${run.scenario}`, [baseFile(run.scenario), baseFile(run.scenario, true), LEDGER]);
    console.log(`level-1 reference set for ${run.scenario} (not a win: confirm moves at level 2/3)`);
    return 0;
  }
  if (!base) {
    if (run.git && head && !sameCode(run.git.sha, head)) throw new Error(`run measured ${run.git.sha} but HEAD is ${head}: bench again`);
    const g = guard(c);
    if (!g.ok) throw new Error(`guard fails at baseline; a red guard protects nothing. Fix it first:\n${g.tail}`);
    write(baseFile(run.scenario), run);
    write(baseFile(run.scenario, true), run);
    ledger({ kind: 'init', scenario: run.scenario, label: run.label, git: run.git, guard: g.status, note: o.note });
    commit(`perf: baseline ${run.scenario}`, [baseFile(run.scenario), baseFile(run.scenario, true), LEDGER, ...keepTraces(runFile(file), run)]);
    return console.log(`baseline set for ${run.scenario}`), 0;
  }
  // Not forceable: rigor is what makes a ledger entry mean something.
  if (run.quick) return console.log('NOT accepted: quick runs are for iterating. Bench without --quick (or with --target to stop early when decisive)'), 1;
  if (run.reps < ACCEPT_REPS && !(run.decisive && run.target === o.target))
    return console.log(`NOT accepted: ${run.reps} reps; need ${ACCEPT_REPS}, or a run that stopped decisively on --target ${o.target}`), 1;
  let rows, why0 = null;
  try { rows = compare(base, run, floors(run.scenario)); } catch (e) { why0 = e.message; }
  // Re-baseline: the run is incomparable (parallelism, build mode, spans), or the ruler changed in a
  // way that keeps it comparable (a new route channel, new counters). Explicit, guarded, claims nothing.
  if (why0 || (o.force && !o.target)) {
    if (!o.force || !o.note) return console.log(`NOT accepted: ${why0}\nTo adopt this run as the new ruler: --force --note "re-baseline: <why>"`), 1;
    const e = { message: why0 ?? 'ruler changed (forced, no target)' };
    if (run.git && head && !sameCode(run.git.sha, head)) throw new Error(`run measured ${run.git.sha} but HEAD is ${head}: bench again`);
    const g = guard(c);
    if (!g.ok) return console.log(`\nNOT accepted: guard failed (cannot be forced)\n${g.tail}`), 1;
    write(baseFile(run.scenario), run);
    write(epochFile(run.scenario), run);
    ledger({ kind: 'rebaseline', scenario: run.scenario, label: run.label, git: run.git, guard: g.status, why: e.message, note: o.note });
    commit(`perf: re-baseline ${run.scenario}: ${o.note}`, [baseFile(run.scenario), epochFile(run.scenario), LEDGER, ...keepTraces(runFile(file), run)]);
    return 0;
  }
  if (!o.target) throw new Error('--target <metric> required');
  const t = rows.find((r) => r.metric === o.target);
  const why = [];
  if (t?.verdict !== 'improved') why.push(`target ${o.target} is ${t?.verdict ?? 'absent'}`);
  for (const r of rows.filter(blocking)) why.push(`${r.metric} ${r.verdict} ${r.delta}`);
  if (run.build && run.build === base.build && run.git?.sha !== base.git?.sha)
    why.push(`installed app binary identical to baseline (${run.build}) though the code changed: a stale build, or a change that compiles to nothing`);
  if (run.git && head && !sameCode(run.git.sha, head)) why.push(`run measured ${run.git.sha} but HEAD is ${head}: bench again`);
  if (run.git?.dirty) why.push('working tree dirty: commit the move so the ledger points at it');
  for (const e of rulerEdits(base.git?.sha)) why.push(`ruler edited since baseline: ${e}`);
  table(rows);
  if (why.length && !o.force) return console.log(`\nNOT accepted:\n- ${why.join('\n- ')}`), 1;
  if (why.length && !o.note) throw new Error('--force needs --note explaining why');
  const g = guard(c); // last, it can be slow; and never forceable
  if (!g.ok) return console.log(`\nNOT accepted: guard failed (cannot be forced)\n${g.tail}`), 1;
  write(baseFile(run.scenario), run);
  ledger({ kind: o.force && why.length ? 'forced' : 'accept', scenario: run.scenario, label: run.label, git: run.git, target: o.target, guard: g.status,
    deltas: Object.fromEntries(rows.filter((r) => r.d !== undefined).map((r) => [r.metric, r.delta])), overrode: why, note: o.note });
  console.log(`\naccepted: ${o.target} ${t?.delta}`);
  commit(`perf: accept ${run.label}: ${o.target} ${t?.delta}`, [baseFile(run.scenario), LEDGER, ...keepTraces(runFile(file), run)]);
  return 0;
}

// Log the failed hypothesis and revert the move: every commit since the baseline that touches code
// outside perf/ (accept/stability commits are perf/-only and stay). --keep logs without reverting.
function reject(file, o) {
  if (!o.note) throw new Error('--note required: what was tried and why it did not move the metric');
  const run = read(runFile(file));
  const base = baseline(run.scenario);
  const rows = base ? compare(base, run, floors(run.scenario)) : [];
  const g = git();
  let reverted = [];
  if (g && base?.git && !o.keep) {
    if (g.sha !== run.git?.sha) throw new Error(`HEAD ${g.sha} is not the measured move ${run.git?.sha}; revert by hand or pass --keep`);
    if (g.dirty) throw new Error('working tree dirty: commit or stash before reject reverts the move');
    // From the last settled point: the baseline, or where the last reject left the code. Older moves
    // are already accepted or reverted; reverting them again only churns history.
    const settled = ledgerEntries().filter((e) => e.scenario === run.scenario && e.kind === 'reject' && e.settled).pop()?.settled;
    const from = settled && gitx('merge-base', '--is-ancestor', base.git.sha, settled).status === 0 ? settled : base.git.sha;
    const shas = gitx('rev-list', `${from}..${g.sha}`).stdout.split('\n').filter(Boolean); // newest first
    reverted = shas.filter((s) => gitx('diff-tree', '--no-commit-id', '--name-only', '-r', s).stdout.split('\n').some((f) => f && !f.startsWith('perf/')));
    if (reverted.length) {
      const r = gitx('revert', '--no-edit', ...reverted);
      if (r.status !== 0) {
        gitx('revert', '--abort');
        throw new Error(`git revert failed, nothing changed:\n${(r.stderr || r.stdout).trim()}`);
      }
      console.log(`reverted ${reverted.map((s) => s.slice(0, 7)).join(' ')}`);
    }
  }
  ledger({ kind: 'reject', scenario: run.scenario, label: run.label, git: run.git, note: o.note, reverted, settled: git()?.sha,
    deltas: Object.fromEntries(rows.filter((r) => r.d !== undefined).map((r) => [r.metric, `${r.delta} ${r.verdict}`])) });
  commit(`perf: reject ${run.label}`, [LEDGER]);
  return 0;
}

function report(name) {
  const names = name ? [name] : fs.readdirSync(path.join(DIR, 'baseline')).filter((f) => !/\.(init|epoch)\.json$/.test(f)).map((f) => f.replace('.json', ''));
  for (const n of names) {
    const epoch = fs.existsSync(epochFile(n));
    console.log(`\n## ${n}${epoch ? ' (since the last re-baseline; earlier gains are in the ledger below)' : ''}`);
    table(compare(read(epoch ? epochFile(n) : baseFile(n, true)), read(baseFile(n)), floors(n)));
  }
  const log = ledgerEntries();
  console.log(`\nmoves: ${log.filter((e) => e.kind === 'accept').length} accepted, ${log.filter((e) => e.kind === 'reject').length} rejected, ${log.filter((e) => e.kind === 'forced').length} forced`);
  for (const e of log.filter((e) => e.kind !== 'init' && (!name || e.scenario === name))) console.log(`  ${e.kind.padEnd(7)} ${e.scenario} ${e.label} ${e.git?.sha ?? ''} ${e.target ? `${e.target} ${e.deltas?.[e.target]}` : ''} ${e.note ?? ''}`);
  return 0;
}

// ---------- selftest ----------

function selftest() {
  const assert = (c, m) => { if (!c) throw new Error(`selftest: ${m}`); };
  const run = (samples, extra = {}) => ({ dev: false, platform: 'ios', device: 'x', spans: {}, samples, ...extra });
  const v = (rows, k) => rows.find((r) => r.metric === k).verdict;
  const t = [100, 104, 98, 101, 99, 103, 97];

  assert(v(compare(run({ a: t }), run({ a: t })), 'a') === 'noise', 'identical runs are noise');
  assert(v(compare(run({ a: t }), run({ a: t.map((x) => x * 0.7) })), 'a') === 'improved', '30% faster is improved');
  assert(v(compare(run({ a: t }), run({ a: t.map((x) => x * 1.3) })), 'a') === 'regressed', '30% slower is regressed');
  assert(v(compare(run({ a: t }), run({ a: t.map((x) => x * 0.98) })), 'a') === 'noise', '2% is under MIN_EFFECT');
  assert(v(compare(run({ n: [2, 2, 2] }), run({ n: [1, 1, 1] })), 'n') === 'improved', 'count 2->1 is improved');
  assert(v(compare(run({ n: [0, 0] }), run({ n: [1, 1] })), 'n') === 'regressed', 'count 0->1 is regressed');
  assert(v(compare(run({ 'fixture:msgs': [5000] }), run({ 'fixture:msgs': [4000] })), 'fixture:msgs') === 'MISMATCH', 'fixture change flagged');
  assert(v(compare(run({ a: t }), run({})), 'a') === 'gone', 'vanished metric flagged');
  assert(JSON.stringify(compare(run({ a: t }), run({ a: [80, 90, 85] }))) === JSON.stringify(compare(run({ a: t }), run({ a: [80, 90, 85] }))), 'deterministic');
  let threw = false;
  try { compare(run({}), run({}, { dev: true })); } catch { threw = true; }
  assert(threw, 'dev vs release refused');

  const perf = { epoch0: 1000, marks: [{ n: 'open', t: 10 }, { n: 'ready', t: 30 }, { n: 'open', t: 50 }, { n: 'ready', t: 55 }, { n: 'ready', t: 70 }] };
  const sp = spans(perf, { o: ['open', 'ready'], s: ['@launch', 'ready'] }, 1005);
  assert(sp.o === 20 && sp['o#2'] === 5 && sp.s === 25 && !('s#2' in sp), `span pairing ${JSON.stringify(sp)}`);
  assert(med([3, 1, 2]) === 2 && med([4, 1, 2, 3]) === 2.5, 'median');
  const ev = parseEvents([
    '{"k":"meta","debug":false,"t":1}', '{"k":"m","n":"open","t":5}', '{"k":"m","n":"open","t":100}',
    '{"k":"c","n":"net:GET /x","v":1,"t":101}', '{"k":"c","n":"net:GET /x","v":2,"t":300}', '{"k":"m","n":"ready","t":150.5}',
    '{"k":"f","n":"msgs","v":10,"t":102}', '{"k":"f","n":"msgs","v":3000,"t":103}'].join('\n'), 100);
  assert(ev.dev === false && ev.marks.length === 2 && ev.counts['net:GET /x'] === 3 && ev.counts['fixture:msgs'] === 3000, `parseEvents ${JSON.stringify(ev)}`);
  assert(spans(ev, { o: ['open', 'ready'] }, 100).o === 50.5, 'native span');

  const tmp = fs.mkdtempSync(path.join(process.env.TMPDIR ?? '/tmp', 'perf-detect-'));
  const mk = (f, body = '') => (fs.mkdirSync(path.dirname(path.join(tmp, f)), { recursive: true }), fs.writeFileSync(path.join(tmp, f), body));
  mk('app/ios/App.xcodeproj/project.pbxproj'); mk('app/package.json', '{"dependencies":{"react-native":"0.86.0"}}');
  assert(detect(tmp).runtime === 'rn' && detect(tmp).root === 'app', 'RN beats its ios/ project');
  fs.rmSync(path.join(tmp, 'app/package.json'));
  mk('app/ios/A.swift', 'import SwiftUI\n'); mk('app/ios/B.swift', 'import SwiftUI\n'); mk('app/ios/C.swift', 'import UIKit\n');
  mk('app/node_modules/x/X.swift', 'import UIKit\n'); mk('app/node_modules/x/Y.swift', 'import UIKit\n');
  const d = detect(tmp);
  assert(d.runtime === 'native' && d.flavour === 'swiftui', `native swiftui, node_modules skipped ${JSON.stringify(d)}`);
  fs.rmSync(tmp, { recursive: true });

  const t9 = t.map((x) => x * 0.92); // 8% faster
  assert(v(compare(run({ a: t }), run({ a: t9 })), 'a') === 'improved', '8% passes the default floor');
  assert(v(compare(run({ a: t }), run({ a: t9 }), { a: 0.1 }), 'a') === 'noise', '8% is noise under a 10% stability floor');

  // decisive(): counts settle at 3 reps; a guardrail drifting the wrong way, undecided, keeps sampling
  const rowsOf = (A, B) => compare(run(A), run(B));
  assert(decisive(rowsOf({ n: [2, 2, 2], a: t }, { n: [1, 1, 1], a: t.slice(0, 3) }), 'n'), 'count target with a flat guardrail is decisive');
  assert(!decisive(rowsOf({ n: [2, 2, 2], a: [100, 101, 99] }, { n: [1, 1, 1], a: [99, 106, 103] }), 'n'), 'guardrail drifting up, undecided: keep going');
  assert(!decisive(rowsOf({ a: t }, { a: [95, 104, 99] }), 'a'), 'noisy timing target at 3 reps is not decisive');
  threw = false;
  try { compare(run({}), run({}, { parallel: 3 })); } catch { threw = true; }
  assert(threw, 'serial vs parallel refused');

  const pm = parseMetrics(['noise', 'META debug=false', 'METRIC mount_ms=12.5', 'Test Case foo: METRIC count:body:Row=20',
    'METRIC count:net:GET /chats/:id/messages=1', 'METRIC mount_ms=11', 'FIXTURE messages=3000'].join('\n'));
  assert(pm.dev === false && pm.samples.mount_ms.join() === '12.5,11' && pm.samples['count:body:Row'][0] === 20 &&
    pm.samples['count:net:GET /chats/:id/messages'][0] === 1 && pm.samples['fixture:messages'][0] === 3000, `parseMetrics ${JSON.stringify(pm)}`);
  for (const f of ['src/perf.ts', 'App/Perf.swift', 'App/PerfRoutes.swift', 'src/perfRoutes.tsx', 'App/PerfFixture.swift', 'Tests/PerfUnitTests.swift', 'perf/bench/chat.perf.test.tsx', 'src/chat.perf.test.tsx'])
    assert(GUARDED.some((re) => re.test(f)), `ruler guards ${f}`);
  for (const f of ['src/Performance.swift', 'src/App.tsx', 'src/perfect.ts'])
    assert(!GUARDED.some((re) => re.test(f)), `ruler leaves ${f} alone`);

  selftestGit(assert);
  console.log('selftest ok');
  return 0;
}

// accept/reject drive git for real: run them as child processes in a throwaway repo.
function selftestGit(assert) {
  const tmp = fs.mkdtempSync(path.join(process.env.TMPDIR ?? '/tmp', 'perf-git-'));
  const env = { ...process.env, GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@t', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@t' };
  const g = (...a) => spawnSync('git', a, { cwd: tmp, encoding: 'utf8', env });
  const self = new URL(import.meta.url).pathname;
  const perf = (...a) => spawnSync(process.execPath, [self, ...a], { cwd: tmp, encoding: 'utf8', env });
  const put = (f, s) => (fs.mkdirSync(path.dirname(path.join(tmp, f)), { recursive: true }), fs.writeFileSync(path.join(tmp, f), s));
  const sha = () => g('rev-parse', '--short', 'HEAD').stdout.trim();
  const move = (body, msg) => (put('app.txt', body), g('commit', '-qam', msg), sha());
  const runAt = (label, ms, extra = {}) => {
    const f = `perf/runs/s/${label}.json`;
    put(f, JSON.stringify({ scenario: 's', label, git: { sha: sha(), dirty: false }, device: 'x', platform: 'ios', dev: false, spans: {}, samples: { m: ms }, reps: ms.length, ...extra }));
    return f;
  };
  const ms = (x) => [x, x + 2, x - 1, x + 1, x - 2, x, x + 1];
  const log = () => g('log', '--format=%s').stdout.trim().split('\n');

  g('init', '-q');
  put('.gitignore', 'perf/runs/\n');
  put('perf/perf.config.json', JSON.stringify({ runtime: 'rn', platform: 'ios', device: 'x', guard: 'grep -q ^ok app.txt', scenarios: { s: { flow: 'f' } } }));
  put('app.txt', 'ok slow\n');
  g('add', '-A'); g('commit', '-qm', 'app');

  let r = perf('accept', runAt('base', ms(100)));
  assert(r.status === 0 && log()[0] === 'perf: baseline s', `baseline commits: ${r.stdout}${r.stderr}`);

  move('ok fast\n', 'fast');
  r = perf('accept', runAt('fast-q', ms(70).slice(0, 3), { quick: true }), '--target', 'm', '--force', '--note', 'x');
  assert(r.status === 1 && /quick runs/.test(r.stdout), `quick run refused even forced: ${r.stdout}`);
  r = perf('accept', runAt('fast-3', ms(70).slice(0, 3)), '--target', 'm');
  assert(r.status === 1 && /need 5/.test(r.stdout), `3 plain reps refused: ${r.stdout}`);
  r = perf('accept', runAt('fast-d', ms(70).slice(0, 3), { decisive: true, target: 'other' }), '--target', 'm');
  assert(r.status === 1 && /need 5/.test(r.stdout), `decisive on another target refused: ${r.stdout}`);
  r = perf('accept', runAt('fast', ms(70).slice(0, 3), { decisive: true, target: 'm' }), '--target', 'm');
  assert(r.status === 0 && log()[0].startsWith('perf: accept fast: m -30'), `accept commits: ${r.stdout}${r.stderr}`);

  move('BAD but faster\n', 'cheat');
  const cheat = runAt('cheat', ms(50));
  r = perf('accept', cheat, '--target', 'm', '--force', '--note', 'x');
  assert(r.status === 1 && /guard failed/.test(r.stdout) && log()[0] === 'cheat', `guard blocks even --force: ${r.stdout}${r.stderr}`);

  r = perf('reject', cheat, '--note', 'broke behaviour');
  const body = fs.readFileSync(path.join(tmp, 'app.txt'), 'utf8');
  assert(r.status === 0 && body === 'ok fast\n' && log()[0] === 'perf: reject cheat' && log()[1].startsWith('Revert "cheat"'), `reject reverts: ${r.stdout}${r.stderr} ${log()}`);
  assert(read(path.join(tmp, 'perf/baseline/s.json')).label === 'fast', 'reject leaves the baseline alone');
  const noop = (move('ok fast\n\n', 'noop'), runAt('noop', ms(70)));
  r = perf('reject', noop, '--note', 'no-op');
  assert(r.status === 0 && /reverted [0-9a-f]{7}\n/.test(r.stdout) && !/ .* /.test(r.stdout.match(/reverted (.*)/)[1]), `second reject reverts only its own move: ${r.stdout}`);
  assert(fs.readFileSync(path.join(tmp, 'app.txt'), 'utf8') === 'ok fast\n', 'code back to the accepted state');
  const other = runAt('other', ms(70)); // same code, a second scenario: harness commits must not invalidate it
  g('commit', '--allow-empty', '-qm', 'perf: bookkeeping'); put('perf/ledger.jsonl', fs.readFileSync(path.join(tmp, 'perf/ledger.jsonl'), 'utf8') + '\n'); g('commit', '-qam', 'perf: ledger touch');
  r = perf('accept', other, '--target', 'm');
  assert(!/HEAD is/.test(r.stdout + r.stderr), `perf/-only commits since the run are fine: ${r.stdout}${r.stderr}`);
  const par = runAt('par3', ms(80), { parallel: 3 });
  r = perf('accept', par, '--target', 'm');
  assert(r.status === 1 && /parallel/.test(r.stdout), `new parallelism needs a forced re-baseline: ${r.stdout}`);
  r = perf('accept', par, '--force', '--note', 'go parallel');
  assert(r.status === 0 && log()[0] === 'perf: re-baseline s: go parallel' && fs.existsSync(path.join(tmp, 'perf/baseline/s.epoch.json')), `rebaseline commits: ${r.stdout}${r.stderr}`);
  r = perf('report', 's');
  assert(r.status === 0 && /since the last re-baseline/.test(r.stdout), `report works across a re-baseline: ${r.stdout}${r.stderr}`);
  r = perf('accept', runAt('unit', ms(50), { tier: 'unit', scenario: 's' }), '--target', 'm');
  assert(r.status === 0 && log()[0] === 'perf: level-1 reference s' && /not a win/.test(r.stdout), `level-1 runs only set a reference: ${r.stdout}${r.stderr}`);
  put('build/App.app/App', 'xx PerfRoutes yy');
  assert(perf('leakcheck', 'build/App.app').status === 1, 'leakcheck catches a marker');
  put('build/Clean.app/App', 'nothing here');
  assert(perf('leakcheck', 'build/Clean.app').status === 0, 'leakcheck passes a clean build');
  fs.rmSync(tmp, { recursive: true });
}

// ---------- cli ----------

const [cmd, ...rest] = process.argv.slice(2);
const pos = [], opt = {};
for (let i = 0; i < rest.length; i++) {
  const a = rest[i];
  if (a.startsWith('--no-')) opt[a.slice(5)] = false;
  else if (a.startsWith('--')) opt[a.slice(2)] = rest[i + 1] && !rest[i + 1].startsWith('--') ? rest[++i] : true;
  else pos.push(a);
}
const cmds = {
  bench: () => bench(pos[0], opt),
  profile: () => profile(pos[0], opt),
  compare: () => { const a = read(pos[0]); const rows = compare(a, read(pos[1]), floors(a.scenario)); table(rows); return rows.some(blocking) ? 1 : 0; },
  stability: () => stability(pos[0], opt),
  devices: () => devices(+pos[0], opt),
  leakcheck: () => leakcheck(pos),
  _rep: () => { // worker for parallel benches: one rep on --device, JSON on the last stdout line
    const c = config({ device: false });
    c.device = opt.device; c.devices = [opt.device];
    console.log(JSON.stringify(rep(c, c.scenarios[pos[0]])));
    return 0;
  },
  guard: () => { const g = guard(config({ device: false })); console.log(`guard ${g.status}${g.tail ? `\n${g.tail}` : ''}`); return g.ok ? 0 : 1; },
  accept: () => accept(pos[0], opt),
  reject: () => reject(pos[0], opt),
  report: () => report(pos[0]),
  detect: () => (console.log(JSON.stringify(detect(pos[0] ?? ROOT), null, 2)), 0),
  selftest,
};
if (!cmds[cmd]) {
  console.log(fs.readFileSync(new URL(import.meta.url), 'utf8').split('\n').filter((l) => l.startsWith('//   perf.mjs')).map((l) => l.slice(3)).join('\n'));
  process.exit(cmd ? 2 : 0);
}
try {
  process.exit(await cmds[cmd]());
} catch (e) {
  console.error(`error: ${e.message}`);
  process.exit(2);
}
