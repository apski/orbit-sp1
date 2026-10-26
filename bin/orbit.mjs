#!/usr/bin/env node
// orbit: Orbit plus service pack 1 (orbit-SP1). Pack commands, else the Orbit engine for the current repo.
// No dependencies beyond node, python3.13+, git.
//
//   orbit install <repo> [--only navlab,orbit,spectra]   NavLab (navigation, DS audit, architecture pack; RN repos)
//                                 + Orbit (code index) + Spectra (UI compiler) + orbit-rag (docs/decisions RAG:
//                                 index, MCP, search UI on 127.0.0.1:7420; --port n) + Voyager-1 (job -> validated
//                                 draft PR: .voyager/profile.toml adapter) + gate (oracle, must-pass list, ratchet as a
//                                 Stop hook; RN repos) + the orbit-sp1 skill; then status
//   orbit status [repo]           each subsystem: installed? L1 bootstrap done? the next step
//   orbit installer-check <subsystem> <fixture repo>   the installer contract on a copy of the fixture: idempotent,
//                                 nothing machine-specific written, status row shape (see ORBIT-SP1.md)
//   orbit sim claim|release|mode  the iOS simulator a pack tool may drive: a slim orbitfleet lease by default
//                                 (`argent-sim slim|full`), a pinned udid when given (bin/sim.mjs)
//   orbit cloud up|run|sync|ssh|ls|down  where a command runs: local (this Mac) or do (a throwaway DigitalOcean
//                                 Linux box with zsh, node, python and the pack; ssh from this IP only; bin/cloud.mjs)
//   orbit voyager <cmd> ...       Voyager-1: job -> validated draft PR (job, validate, strengthen, constraints, backtest)
//   orbit gate <cmd> ...          the agent gate (propose, ratchet, contracts, flows, stop, selftest; gate/gate.py)
//   orbit compare <other-orbit>   this pack vs another Orbit checkout: new / changed / missing, as markdown evidence
//   orbit sync [--check] [--force]  everything user-level from this checkout: rules (~/.claude/rules, incl. the step-0
//                                 line in argent's rules), tools (~/.local/bin symlinks), skills (skills-sync)
//   orbit skills-sync [--check] [--force] [--to <dir>] [--install-hooks]
//                                 every */skills/<name> in this pack -> your global skills dir as REAL files (no links);
//                                 edits made there are detected and never overwritten without --force
//   orbit <engine command> ...    index, how, observe, refs, packet, principles, verify, perf, ... with the nearest
//                                 .orbit/orbit.toml above the current directory (same as the repo's .orbit/orbit)
//
// Nothing machine-specific is written into the repo: launchers in <repo>/.orbit/ find this checkout at run time
// ($ORBIT_HOME, else ~/code/orbit-sp1), so they can be committed and work on any machine.
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, cpSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { homedir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOME = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const [cmd, target] = argv.filter((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--'));
const only = argv.includes('--only') ? argv[argv.indexOf('--only') + 1].split(',') : ['navlab', 'orbit', 'spectra', 'orbit-rag', 'voyager', 'perf', 'gate'];
// orbit-rag's own flags pass through to orbit-rag/install.mjs
const ragFlags = ['--port', '--no-service', '--no-mcp'].flatMap(f => (!argv.includes(f) ? [] : f === '--port' ? [f, argv[argv.indexOf(f) + 1]] : [f]));
const read = p => { try { return readFileSync(p, 'utf8'); } catch { return ''; } };
const json = p => { try { return JSON.parse(read(p)); } catch { return null; } };
const run = (bin, args, opts = {}) => spawnSync(bin, args, { encoding: 'utf8', ...opts });
let rev = null;
try { rev = execFileSync('git', ['-C', HOME, 'rev-parse', '--short', 'HEAD'], { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim(); } catch {}

/** Shell prelude every launcher starts with: find the orbit-SP1 checkout (marker: ORBIT-SP1.md) into $op. */
export const FIND = `op=""
for d in "$ORBIT_HOME" "$HOME/code/orbit-sp1"; do
  [ -n "$d" ] && [ -f "$d/ORBIT-SP1.md" ] && { op="$d"; break; }
done
[ -n "$op" ] || { echo "orbit-SP1 checkout not found: clone it to ~/code/orbit-sp1 or set ORBIT_HOME" >&2; exit 127; }
here="$(cd "$(dirname "$0")" && pwd)"`;
const PYTHON = `py=""; for p in python3.15 python3.14 python3.13 python3; do command -v "$p" >/dev/null 2>&1 && { py="$p"; break; }; done`;

const LAUNCHERS = {
  orbit: `# Orbit (code index) for this repo; config: orbit.toml next to this file.\nORBIT_CONFIG="$here/orbit.toml" exec "$op/orbit/bin/orbit" "$@"`,
  census: `# Orbit bootstrap census (read-only): census | propose | report. A later --repo overrides this repo.\n${PYTHON}\nexec "$py" "$op/orbit/bootstrap/census.py" --repo "$here/.." "$@"`,
  'engine-tests': `# The Orbit engine's own test suite (runs in the orbit-SP1 checkout).\n${PYTHON}\ncd "$op/orbit" && exec "$py" -m unittest discover -s tests -t . "$@"`,
  home: `# Prints the orbit-SP1 checkout this repo uses.\necho "$op"`,
};

/** A repo that already runs its own Orbit (engine + config in agents/orbit; Rust core, its own /orbit skill, streams,
 *  DECISIONS.md). That Orbit is the high level: the pack installs its subsystems INTO it (a block in its ORBIT.md, the
 *  index its agents read first), never a second engine beside it, and never over its skills. the host's reconcile plan says which
 *  side wins per subsystem. */
const hostOrbit = repo => (existsSync(join(repo, 'agents', 'orbit', 'bin', 'orbit')) ? join(repo, 'agents', 'orbit') : null);

/** Skills go where the repo keeps them: real files in .agents/skills; .claude/skills gets a relative link when the
 *  repo already links .claude -> .agents, else its own copy. Placeholders are filled with run-time expressions. */
function placeSkills(src, repo, vars) {
  const names = readdirSync(src, { withFileTypes: true }).filter(d => d.isDirectory() && existsSync(join(src, d.name, 'SKILL.md'))).map(d => d.name);
  const claude = join(repo, '.claude', 'skills'), agents = join(repo, '.agents', 'skills');
  const isLink = p => { try { return lstatSync(p).isSymbolicLink(); } catch { return false; } };
  const linksConvention = existsSync(claude) && readdirSync(claude).some(n => isLink(join(claude, n)));
  // before markers existed a prior install's pack names were ours; a host-Orbit repo only ever had marked installs
  const priorInstall = existsSync(join(repo, '.orbit', 'orbit-SP1.json')) && !hostOrbit(repo);
  const skipped = [];
  for (const n of names) {
    // a skill the pack didn't place (no marker) is the repo's own: it wins; the host's reconcile plan decides merges
    if ([join(agents, n), join(claude, n)].some(d => existsSync(d) && !isLink(d) && !existsSync(join(d, '.orbit-sp1.json')) && !priorInstall)) { skipped.push(n); continue; }
    const mark = d => writeFileSync(join(d, '.orbit-sp1.json'), JSON.stringify({ source: relative(HOME, join(src, n)) }) + '\n');
    const fill = dst => { let t = read(join(dst, 'SKILL.md')); for (const [k, v] of Object.entries(vars)) t = t.replaceAll(k, v); writeFileSync(join(dst, 'SKILL.md'), t); };
    cpSync(join(src, n), join(agents, n), { recursive: true }); fill(join(agents, n)); mark(join(agents, n));
    const c = join(claude, n);
    if (isLink(c)) continue;  // already points at .agents/skills/<n>
    mkdirSync(claude, { recursive: true });
    if (linksConvention && !existsSync(c)) symlinkSync(join('..', '..', '.agents', 'skills', n), c);
    else { cpSync(join(src, n), c, { recursive: true }); fill(c); mark(c); }
  }
  if (skipped.length) console.error(`skills kept (the repo has its own): ${skipped.join(', ')}`);
  return names.filter(n => !skipped.includes(n));
}

function install(repo) {
  const log = [];
  const dot = join(repo, '.orbit');
  mkdirSync(dot, { recursive: true });
  const launcher = name => { writeFileSync(join(dot, name), `#!/bin/sh\n${FIND}\n${LAUNCHERS[name]}\n`); chmodSync(join(dot, name), 0o755); };
  launcher('home');  // every subsystem's skills find orbit-SP1 through it
  const pkg = json(join(repo, 'package.json')) ?? {};
  const rn = { ...pkg.dependencies, ...pkg.devDependencies }['react-native'];

  // 1. NavLab: navigation map + architecture/DS pack (React Native repos). Its scanner needs one npm dependency.
  if (only.includes('navlab')) {
    if (!rn) log.push(['navlab', 'skipped', 'not a React Native repo (no react-native in package.json)']);
    else {
      const nav = join(HOME, 'navlab');
      if (!existsSync(join(nav, 'scan', 'node_modules'))) run('npm', ['install', '--silent', '--prefix', join(nav, 'scan')], { stdio: 'inherit' });
      const n = run('node', [join(nav, 'bootstrap.mjs'), repo, '--no-skills']);
      placeSkills(join(nav, 'skills'), repo, { $NAVLAB_HOME: '$(.orbit/home)/navlab' });  // run-time lookup, not this machine's path
      log.push(['navlab', n.status === 0 ? 'installed' : 'FAILED', (n.status === 0 ? n.stdout : n.stderr).trim().split('\n').slice(-1)[0]]);
    }
  }

  // 2. Orbit: launchers + config dir <repo>/.orbit (the engine stays in orbit-SP1), skills, census report.
  const host = hostOrbit(repo);
  if (only.includes('orbit') && host) {
    log.push(['orbit', 'host', `${relative(repo, host)} is the Orbit; SP1 subsystems wired into its ORBIT.md; reconcile per its own plan`]);
  } else if (only.includes('orbit')) {
    for (const name of Object.keys(LAUNCHERS)) launcher(name);
    if (!existsSync(join(dot, '.gitignore'))) writeFileSync(join(dot, '.gitignore'), '# Orbit index and run output stay local.\n.cache/\nruns/\ncensus.md\n');
    const orbitSkills = placeSkills(join(HOME, 'orbit', 'skills'), repo, {});
    const cfg = existsSync(join(dot, 'orbit.toml'));
    // the census runs last (below), after every subsystem has written its files, so a re-install sees the same tree
    log.push(['orbit', 'installed', `${orbitSkills.join(', ')} skills; .orbit/census.md; ${cfg ? 'config present' : 'config pending (orbit-bootstrap; starter: orbit/examples/)'}`]);
  }

  // 3. Spectra: UI compiler (its own install: skills, .spectra launcher, jest harness, profile).
  if (only.includes('spectra')) {
    const s = run('node', [join(HOME, 'spectra', 'bin', 'spectra.mjs'), 'install', repo, '--no-skills']);
    if (s.status === 0) placeSkills(join(HOME, 'spectra', 'skills'), repo, { $NAVLAB_HOME: '$(.orbit/home)/navlab' });
    log.push(['spectra', s.status === 0 ? 'installed' : 'FAILED', s.status === 0 ? 'profile written; jest harness pending (spectra-bootstrap)' : s.stderr.trim()]);
  }

  // 4. orbit-rag: docs/decisions RAG (its own install: build, <repo>/.orbit-rag index, MCP, search UI service).
  if (only.includes('orbit-rag')) {
    const r = run('node', [join(HOME, 'orbit-rag', 'install.mjs'), repo, ...ragFlags]);
    const rows = (r.stdout || r.stderr).trim().split('\n').filter(Boolean).map(l => l.split('  '));
    for (const [, step, ...note] of rows) log.push(['orbit-rag', step === 'FAILED' ? 'FAILED' : step, note.join('  ')]);
  }

  // 5. Voyager-1: job -> validated PR layer (its own install: .voyager/profile.toml adapter + launcher).
  if (only.includes('voyager')) {
    const v = run(join(HOME, 'voyager', 'bin', 'voyager'), ['install', repo]);
    placeSkills(join(HOME, 'voyager', 'skills'), repo, {});
    const [, st, ...note] = (v.stdout || v.stderr).trim().split('\n').slice(-1)[0].split('  ');
    log.push(['voyager', v.status === 0 ? st : 'FAILED', note.join('  ')]);
  }

  // 6. Perf pack: skills only (thread-offload, perf-climb, ...); their state lives in each skill's own dirs.
  if (only.includes('perf')) {
    const names = placeSkills(join(HOME, 'perf', 'skills'), repo, {});
    log.push(['perf', 'installed', `${readdirSync(join(HOME, 'perf', 'skills')).length} skills${names.length ? ` (kept the repo's own: ${names.join(', ')})` : ''}`]);
  }

  // 6b. Gate: oracle + must-pass list + ratchet as a Claude Code Stop hook (RN repos: its checks are NavLab's).
  if (only.includes('gate')) {
    if (!rn) log.push(['gate', 'skipped', 'not a React Native repo (no react-native in package.json)']);
    else {
      const g = run(join(HOME, 'gate', 'bin', 'gate'), ['install', repo]);
      placeSkills(join(HOME, 'gate', 'skills'), repo, {});
      const [, st, ...note] = (g.stdout || g.stderr).trim().split('\n').slice(-1)[0].split('  ');
      log.push(['gate', g.status === 0 ? st : 'FAILED', note.join('  ')]);
    }
  }

  // 7. Simulators: move the repo to slim sims (orbitfleet leases via bin/sim.mjs) on this run. Pack-owned configs that
  // pin a simulator are set to "fleet"; a physical phone stays pinned; the repo's own files are only reported.
  log.push(['sims', ...migrateSims(repo)]);

  // 8. Rules: the pack's Claude Code rules into the repo (.claude/rules), so every agent working there gets them.
  const ruleRows = placeRules(join(repo, '.claude', 'rules'));
  log.push(['rules', 'installed', ruleRows.map(([n, st]) => `${n}: ${st}`).join(', ')]);

  // 9. The index skill + AGENTS.md block that ties them together.
  placeSkills(join(HOME, 'skills'), repo, {});
  writeFileSync(join(dot, 'orbit-SP1.json'), JSON.stringify({ revision: rev, installedAt: new Date().toISOString().slice(0, 10), subsystems: only, ...(host && { hostOrbit: relative(repo, host) }) }, null, 1) + '\n');
  const agents = join(repo, 'AGENTS.md');
  const block = `<!-- orbit-sp1:start -->
## orbit-SP1

Installed: ${only.join(', ')}. Orbit (cited code index): \`.orbit/orbit\`, notes in \`.orbit/README.md\`.
${only.includes('spectra') ? 'Spectra (RN/Figma -> native UI compiler): `.spectra/spectra`. ' : ''}${only.includes('orbit-rag') ? 'orbit-rag (docs/decisions RAG): MCP `orbit-rag`, search UI http://127.0.0.1:7420. ' : ''}${only.includes('navlab') && rn ? 'NavLab: block `navlab:`. ' : ''}${only.includes('voyager') ? 'Voyager-1 (job -> validated draft PR): `.voyager/voyager`, skill `voyager`. ' : ''}${only.includes('gate') && rn ? 'Gate (oracle, must-pass list, ratchet): `.orbit/gate`, loop in `.orbit/CLIMB.md`, skill `gate`. ' : ''}Bring subsystems online with the
\`orbit-sp1\` skill. orbit-SP1 checkout: \`.orbit/home\`.
<!-- orbit-sp1:end -->`;
  const upsert = (f, b) => { const t = read(f); writeFileSync(f, t.includes('<!-- orbit-sp1:start') ? t.replace(/<!-- orbit-sp1:start[\s\S]*?<!-- orbit-sp1:end -->/, b) : `${t}${t ? '\n\n' : ''}${b}\n`); };
  upsert(agents, host ? block.replace(/Orbit \(cited code index\)[^\n]*\n/, `The Orbit is \`${relative(repo, host)}\` (ORBIT.md); orbit-SP1's subsystems are listed in its block there.\n`) : block);
  if (host) upsert(join(host, 'ORBIT.md'), hostBlock(only, !!rn));
  if (only.includes('orbit') && !host) {
    const c = run(join(dot, 'census'), ['report'], { cwd: repo });
    if (c.status === 0) writeFileSync(join(dot, 'census.md'), c.stdout);
    else log.push(['orbit', 'FAILED', 'census: ' + c.stderr.trim().split('\n').slice(-1)[0]]);
  }
  for (const [k, st, note] of log) console.log(`${k.padEnd(8)} ${st.padEnd(10)} ${note}`);
  console.log('');
  status(repo);
}

/** orbit-SP1 subsystems as rows of the host Orbit's own subsystem table, with the reconcile verdict per row. */
function hostBlock(only, rn) {
  const row = (k, ...cells) => (only.includes(k) ? [`| ${cells.join(' | ')} |`] : []);
  return ['<!-- orbit-sp1:start -->', '## orbit-SP1 (service pack, installed into this Orbit)', '',
    `Pack: \`$(.orbit/home)\` (rev ${rev ?? '?'}). Per-subsystem verdicts and merge steps: this Orbit's own reconcile plan.`,
    'Orbit stays the entry point; these are subsystems of it. Nothing here edits DECISIONS.md, streams or principles: changes to those go through the usual O-NNN / stream review.', '',
    '| Subsystem | Job | Command | vs this Orbit |', '|---|---|---|---|',
    ...row('spectra', 'Spectra (SP1)', 'UI compiler: RN (jest or running app) / Figma -> IR -> SwiftUI on FlexLayout (Yoga port), judged against the source\'s own layout', '`.spectra/spectra`, skills `spectra*`', 'compare with this Orbit\'s own UI pipeline before retiring either'),
    ...row('navlab', 'NavLab', 'navigation map (navscan), repo profile, DS audit, architecture pack', '`navlab-install`, `navlab-audit`, `ds-audit`', rn ? 'adds the navigation tree' : 'skipped: not an RN repo'),
    ...row('orbit-rag', 'orbit-rag', 'docs + DECISIONS.md + highlights: hybrid search, cited bundles, drift check', 'MCP `orbit-rag`, http://127.0.0.1:7420', 'adds retrieval over DECISIONS.md / knowledge/; read-only'),
    '| Perf pack | thread-offload, perf-climb, native-perf-climb, ... | global skills (`orbit skills-sync`) | adds |',
    '<!-- orbit-sp1:end -->'].join('\n');
}

function status(repo) {
  const rows = [];
  const nav = json(join(repo, 'navlab.config.json'));
  const navMissing = nav?.profile ? Object.entries(nav.profile).filter(([, v]) => v.status === 'missing').map(([k]) => k) : null;
  const navDetected = nav?.profile ? Object.values(nav.profile).filter(v => v.status === 'detected').length : 0;
  rows.push(['navlab', nav ? 'installed' : 'not installed',
    !nav ? 'orbit install (React Native repos)' : navDetected ? `navlab-install skill: confirm ${navDetected} detected sections${navMissing?.length ? `, fill ${navMissing.join(', ')}` : ''}` : 'online']);
  const dot = join(repo, '.orbit');
  const ob = existsSync(join(dot, 'orbit')), cfg = existsSync(join(dot, 'orbit.toml')), indexed = existsSync(join(dot, '.cache'));
  const host = hostOrbit(repo);
  if (host) rows.push(['orbit', 'host', `${relative(repo, host)} is the Orbit (SP1 block in its ORBIT.md: ${read(join(host, 'ORBIT.md')).includes('orbit-sp1:start') ? 'present' : 'missing, run orbit install'}), reconcile per its own plan`]);
  else rows.push(['orbit', ob ? 'installed' : 'not installed', !ob ? 'orbit install' : !cfg ? 'orbit-bootstrap skill: review .orbit/census.md, write .orbit/orbit.toml' : indexed ? 'online' : '.orbit/orbit index']);
  const sp = json(join(repo, '.spectra', 'profile.json'));
  const harness = existsSync(join(repo, '.spectra', 'jest', 'wrap.js'));
  const proved = sp?.bootstrap;
  const spDetected = sp ? Object.values(sp).filter(v => v?.status === 'detected').length : 0;
  rows.push(['spectra', sp ? 'installed' : 'not installed', !sp ? 'orbit install' : proved ? 'online' : `spectra-bootstrap skill: confirm ${spDetected} sections, jest harness${harness ? '' : ' (missing!)'}, prove one screen`]);
  const rag = run('node', [join(HOME, 'orbit-rag', 'install.mjs'), 'status', repo, ...ragFlags]).stdout.trim().split('  ');
  if (rag.length === 3) rows.push(rag);
  const perfSkills = readdirSync(join(HOME, 'perf', 'skills')).filter(n => existsSync(join(repo, '.agents', 'skills', n, 'SKILL.md')));
  rows.push(['perf', perfSkills.length ? 'online' : 'not installed', perfSkills.length ? `${perfSkills.length} skills; run one (perf-climb, thread-offload, ...)` : 'orbit install --only perf']);
  const vg = run(join(HOME, 'voyager', 'bin', 'voyager'), ['status', repo]).stdout.trim().split('  ');
  if (vg.length >= 3) rows.push([vg[0], vg[1], vg.slice(2).join('  ').replace(/^.*next: /, '')]);
  const gt = run(join(HOME, 'gate', 'bin', 'gate'), ['status', repo]).stdout.trim().split('  ');
  if (gt.length >= 3) rows.push([gt[0], gt[1], gt.slice(2).join('  ').replace(/^.*next: /, '')]);
  const cl = run('node', [join(HOME, 'bin', 'cloud.mjs'), 'status']).stdout.trim().split('  ');
  if (cl.length >= 3) rows.push([cl[0], cl[1], cl.slice(2).join('  ').replace(/^next: /, '')]);
  console.log(`orbit status ${repo}${rev ? ` (orbit-SP1 ${rev})` : ''}`);
  for (const [k, st, next] of rows) console.log(`  ${k.padEnd(8)} ${st.padEnd(14)} next: ${next}`);
  return rows;
}

// compare: file-level evidence between this orbit-SP1 and another Orbit checkout. Judging "better" is the agent's job
// (run both test suites / benchmarks), this only says exactly what differs.
function compare(other) {
  const theirs = ['agents/orbit', 'orbit', '.'].map(p => join(other, p)).find(p => existsSync(join(p, 'orbit', 'cli.py')) || existsSync(join(p, 'bin', 'orbit')));
  if (!theirs) { console.error(`no Orbit engine (orbit/cli.py or bin/orbit) under ${other}`); process.exit(1); }
  const SKIP = /(^|\/)(\.cache|\.?runs|__pycache__|node_modules|\.DS_Store)(\/|$)/;  // caches and run output (gitignored)
  const list = root => {
    const out = new Map();
    const walk = d => { for (const e of readdirSync(d, { withFileTypes: true })) { const p = join(d, e.name), r = relative(root, p); if (SKIP.test(r)) continue; e.isDirectory() ? walk(p) : out.set(r, p); } };
    walk(root); return out;
  };
  const ours = list(join(HOME, 'orbit')), them = list(theirs);
  const added = [...ours.keys()].filter(k => !them.has(k)).sort(), removed = [...them.keys()].filter(k => !ours.has(k)).sort();
  const changed = [...ours.keys()].filter(k => them.has(k) && read(ours.get(k)) !== read(them.get(k))).sort().map(k => {
    const d = run('diff', ['-u', them.get(k), ours.get(k)]).stdout.split('\n');
    return [k, d.filter(l => l.startsWith('+') && !l.startsWith('+++')).length, d.filter(l => l.startsWith('-') && !l.startsWith('---')).length];
  });
  // Python Orbit: cmd_<name> in orbit/cli.py; Rust Orbit: one file per command under core/src/{commands,features}
  const cmds = root => [...read(join(root, 'orbit', 'cli.py')).matchAll(/^def cmd_(\w+)/gm)].map(m => m[1]).concat(
    ['commands', 'features'].flatMap(d => (existsSync(join(root, 'core', 'src', d)) ? readdirSync(join(root, 'core', 'src', d)) : []).filter(f => f.endsWith('.rs') && f !== 'mod.rs').map(f => f.slice(0, -3))));
  const [cOurs, cThem] = [cmds(join(HOME, 'orbit')), cmds(theirs)];
  const otherRoot = theirs.endsWith('agents/orbit') ? dirname(dirname(theirs)) : theirs;
  const has = p => existsSync(join(otherRoot, p));
  const md = [`# orbit-SP1 vs ${theirs}`, '', `orbit-SP1 ${rev ?? '(no git)'}; Orbit snapshot provenance: orbit/PROVENANCE.md.`, '',
    '## Orbit engine', '', `- commands only in orbit-SP1: ${cOurs.filter(c => !cThem.includes(c)).join(', ') || 'none'}`,
    `- commands only in theirs: ${cThem.filter(c => !cOurs.includes(c)).join(', ') || 'none'}`,
    `- files only in orbit-SP1 (${added.length}): ${added.join(', ') || 'none'}`, `- files only in theirs (${removed.length}): ${removed.length <= 60 ? removed.join(', ') || 'none' : Object.entries(Object.groupBy(removed, f => f.split('/')[0])).map(([d, fs]) => `${d}/ (${fs.length})`).join(', ')}`,
    `- changed (${changed.length}), +orbit-SP1 / -theirs lines:`, ...changed.map(([k, a, r]) => `  - ${k}: +${a} -${r}`), '',
    '## Other subsystems', '',
    `- NavLab (navigation, DS audit, architecture pack): ${has('navlab') || has('navlab.config.json') ? 'present there; compare navlab/ by hand' : 'not there: new'}`,
    `- Spectra (RN/Figma -> native UI compiler): ${has('spectra') || has('.spectra') ? 'present there; compare spectra/ by hand' : 'not there: new'}`,
    '',
    '## How to judge', '', '- Run both test suites: `python3 -m unittest discover -s <orbit>/tests -t <orbit>` in each.',
    '- Where both have `bench/`, run the same benchmark on the same repo and compare the numbers.',
    '- Take changes with evidence; keep theirs where theirs is newer or tuned to their repo (orbit.toml, policy.toml).'];
  console.log(md.join('\n'));
}

// skills-sync: the pack is the source of truth; global copies are real directories with a marker (.orbit-sp1.json:
// source + content hash) so drift either way is visible. Git hooks re-run it after commit/merge/checkout ("live").
function skillsSync() {
  const flag = k => argv.includes(`--${k}`);
  const to = argv.includes('--to') ? argv[argv.indexOf('--to') + 1] : join(homedir(), '.claude', 'skills');
  mkdirSync(to, { recursive: true });   // a fresh machine has no ~/.claude/skills yet
  const dest = realpathSync(to);
  const tilde = p => (p.startsWith(homedir()) ? '~' + p.slice(homedir().length) : p);
  const vars = { $NAVLAB_HOME: tilde(join(HOME, 'navlab')) };  // NavLab's placeholder; others resolve at run time
  const walk = (d, out = [], base = d) => { for (const e of readdirSync(d, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (e.name === '.orbit-sp1.json' || e.name === '.DS_Store') continue;
    const p = join(d, e.name); e.isDirectory() ? walk(p, out, base) : out.push([relative(base, p), readFileSync(p)]); } return out; };
  const render = ([rel, buf]) => [rel, rel.endsWith('.md') ? Buffer.from(Object.entries(vars).reduce((t, [k, v]) => t.replaceAll(k, v), buf.toString())) : buf];
  const hash = files => { const h = createHash('sha256'); for (const [rel, buf] of files) h.update(rel).update('\0').update(buf).update('\0'); return h.digest('hex').slice(0, 16); };
  const rows = [];
  for (const root of ['skills', 'orbit/skills', 'navlab/skills', 'spectra/skills', 'perf/skills', 'orbit-rag/skills', 'voyager/skills', 'gate/skills', 'machine/skills', 'orbitfleet/skills']) {
    if (!existsSync(join(HOME, root))) continue;
    for (const name of readdirSync(join(HOME, root)).filter(n => existsSync(join(HOME, root, n, 'SKILL.md')))) {
      const files = walk(join(HOME, root, name)).map(render), want = hash(files), d = join(dest, name);
      let state;
      const isLink = (() => { try { return lstatSync(d).isSymbolicLink(); } catch { return false; } })();
      const marker = !isLink && existsSync(join(d, '.orbit-sp1.json')) ? JSON.parse(read(join(d, '.orbit-sp1.json'))) : null;
      const have = existsSync(d) && !isLink ? hash(walk(d)) : null;
      if (isLink) state = 'link -> replaced';
      else if (!existsSync(d)) state = 'new';
      else if (have === want) state = marker ? 'in sync' : 'adopted';
      else if (!marker) state = 'unmanaged copy differs';
      else if (have !== marker.hash) state = 'edited in global copy';
      else state = 'updated';
      const blocked = (state === 'unmanaged copy differs' || state === 'edited in global copy') && !flag('force');
      if (!flag('check') && !blocked && state !== 'in sync') {
        rmSync(d, { recursive: true, force: true });
        for (const [rel, buf] of files) { mkdirSync(dirname(join(d, rel)), { recursive: true }); writeFileSync(join(d, rel), buf); }
      }
      if (!flag('check') && !blocked) writeFileSync(join(d, '.orbit-sp1.json'), JSON.stringify({ source: `${root}/${name}`, hash: want }) + '\n');
      rows.push([name, root, blocked ? `${state}: kept (diff it, then --force to take the pack's)` : state]);
    }
  }
  if (!flag('quiet') || rows.some(r => !['in sync', 'adopted'].includes(r[2])))
    for (const [n, r, st] of rows) if (!flag('quiet') || st !== 'in sync') console.log(`${n.padEnd(28)} ${r.padEnd(16)} ${st}`);
  console.log(`skills-sync ${flag('check') ? '(check) ' : ''}-> ${tilde(dest)}: ${rows.length} skills, ${rows.filter(r => r[2] !== 'in sync').length} not in sync before this run`);
  if (flag('install-hooks')) {
    for (const h of ['post-commit', 'post-merge', 'post-checkout']) {
      const p = join(HOME, '.git', 'hooks', h), cur = read(p);
      if (cur && !cur.includes('orbit skills-sync') && !cur.includes('orbit sync')) { console.log(`hook ${h} exists and isn't ours: add "bin/orbit skills-sync --quiet" to it by hand`); continue; }
      writeFileSync(p, '#!/bin/sh\n# orbit sync: keep the user-level rules, tools and skills identical to this checkout.\n"$(git rev-parse --show-toplevel)/bin/orbit" sync --quiet || true\n');
      chmodSync(p, 0o755);
    }
    console.log('git hooks installed: post-commit, post-merge, post-checkout');
  }
  if (flag('check')) process.exit(rows.every(r => r[2] === 'in sync') ? 0 : 1);
}


// rules: the pack's Claude Code rules (rules/*.md) are the source; user-level (~/.claude/rules) and repo-level
// (<repo>/.claude/rules) copies are installed from here. A sidecar records the hash we wrote, so an edit made in a
// copy is detected and kept (never silently overwritten). rules/argent.step0.md is a single line the pack keeps in
// argent's own rules/argent.md, which `argent update` rewrites.
const RULES = join(HOME, 'rules');
const sha = t => createHash('sha256').update(t).digest('hex').slice(0, 16);
function placeRules(dest, { check = false, force = false } = {}) {
  mkdirSync(dest, { recursive: true });
  const side = join(dest, '.orbit-sp1-rules.json'), seen = json(side) ?? {}, rows = [];
  for (const f of readdirSync(RULES).filter(f => f.endsWith('.md') && f !== 'argent.step0.md').sort()) {
    const want = read(join(RULES, f)), have = read(join(dest, f));
    let st = !have ? 'new' : have === want ? 'in sync' : seen[f] && sha(have) !== seen[f] ? 'edited in copy' : seen[f] ? 'updated' : 'unmanaged copy differs';
    const blocked = (st === 'edited in copy' || st === 'unmanaged copy differs') && !force;
    if (!check && !blocked && st !== 'in sync') writeFileSync(join(dest, f), want);
    if (!check && !blocked) seen[f] = sha(want);
    rows.push([f, blocked ? `${st}: kept (diff it, then --force)` : st]);
  }
  const argentRules = join(dest, 'argent.md'), step0 = read(join(RULES, 'argent.step0.md')).split('\n').filter(l => l && !l.startsWith('<!--') && !l.includes('-->'))[0];
  if (existsSync(argentRules) && step0) {
    const t = read(argentRules);
    const cur = t.split('\n').find(l => l.startsWith('0. **iOS simulators come from orbitfleet**'));
    if (cur === step0) rows.push(['argent.md step 0', 'in sync']);
    else {
      if (!check) writeFileSync(argentRules, cur ? t.replace(cur, step0) : t.replace('Decision order:\n\n', `Decision order:\n\n${step0}\n`));
      rows.push(['argent.md step 0', cur ? 'updated' : 're-applied (argent update had removed it)']);
    }
  }
  if (!check) writeFileSync(side, JSON.stringify(seen, null, 1) + '\n');
  return rows;
}

/** sync: everything the pack installs at user level, from this checkout: rules, small tools, skills. */
function sync() {
  const check = argv.includes('--check'), force = argv.includes('--force');
  const rows = placeRules(join(homedir(), '.claude', 'rules'), { check, force }).map(([n, st]) => [n, 'rules', st]);
  // the pack's own tools, built from source here (toolchains like go/cargo/node are the machine's)
  const fleet = join(HOME, 'orbitfleet');
  if (!check && run('sh', ['-c', 'command -v go']).status !== 0) rows.push(['orbitfleet', 'build', 'go not found: brew install go, then re-run sync']);
  else if (!check) { const b = run('go', ['build', '-o', 'orbitfleet', '.'], { cwd: fleet }); if (b.status !== 0) rows.push(['orbitfleet', 'build', `FAILED: ${(b.stderr || '').trim().split('\n').slice(-1)[0]}`]); }
  for (const [tool, target] of [['argent-sim', join(HOME, 'bin', 'argent-sim')], ['orbitfleet', join(fleet, 'orbitfleet')], ['simwalk', join(HOME, 'simwalk', 'simwalk.mjs')]]) {
    if (!existsSync(target)) continue;   // orbitfleet before its first build
    const link = join(homedir(), '.local', 'bin', tool);
    let st = 'in sync';
    try { if (realpathSync(link) !== realpathSync(target)) st = 'points elsewhere'; } catch { st = 'new'; }
    if (!check && st !== 'in sync') { mkdirSync(dirname(link), { recursive: true }); rmSync(link, { force: true }); symlinkSync(target, link); }
    rows.push([tool, 'tool (symlink)', st]);
  }
  for (const [n, k, st] of rows) if (!argv.includes('--quiet') || st !== 'in sync') console.log(`${n.padEnd(28)} ${k.padEnd(16)} ${st}`);
  const ok = rows.every(r => r[2] === 'in sync');
  skillsSync();   // prints its own table; exits non-zero under --check when skills drift
  if (check && !ok) process.exit(1);
}


// files the pack owns in a repo (it may rewrite them); everything else is the repo's own
const PACK_OWNED = /^(AGENTS\.md|navlab\.(config|adapter)\.json|\.(orbit|spectra|voyager|navlab|orbit-rag)\/|\.(agents|claude)\/(skills|rules)\/)/;
const PHONE = /^[0-9A-F]{8}-[0-9A-F]{16}$/;   // physical iPhone udid shape; simulator udids are UUIDs
const SIM = /\b[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\b/;
function migrateSims(repo) {
  const moved = [];
  const nav = join(repo, 'navlab.config.json'), n = json(nav);
  if (n?.udid && n.udid !== 'fleet' && !PHONE.test(n.udid)) { n.udid = 'fleet'; writeFileSync(nav, JSON.stringify(n, null, 2) + '\n'); moved.push('navlab.config.json'); }
  const sp = join(repo, '.spectra', 'profile.json'), p = json(sp);
  if (p?.simulator && p.simulator.value !== 'fleet' && !(typeof p.simulator.value === 'string' && PHONE.test(p.simulator.value))) {
    p.simulator = { ...p.simulator, value: 'fleet', note: 'slim simulator leased per run (orbit sim claim); set a udid here to pin one' };
    writeFileSync(sp, JSON.stringify(p, null, 2) + '\n'); moved.push('.spectra/profile.json');
  }
  // the repo's own files: report hard-coded simulator udids, never edit them (installer contract)
  const g = run('git', ['-C', repo, 'grep', '-l', '-I', '-E', '[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', '--', '*.sh', '*.mjs', '*.js', '*.ts', '*.yaml', '*.yml', '*.json', 'AGENTS.md', 'CLAUDE.md']);
  const pinned = (g.stdout || '').split('\n').filter(f => f && !/lock|\.orbit|\.spectra|\.voyager|node_modules|\.xcodeproj|Pods/.test(f))
    .filter(f => /simctl|udid|UDID|destination|simulator/i.test(read(join(repo, f))) && SIM.test(read(join(repo, f)))).slice(0, 10);
  const mode = run('node', [join(HOME, 'bin', 'sim.mjs'), 'mode']).stdout.trim() || 'slim';
  return [moved.length ? 'migrated' : 'slim', `mode ${mode}; ${moved.length ? `-> "fleet": ${moved.join(', ')}` : 'pack configs already on fleet'}${pinned.length ? `; simulator udids hard-coded in repo files (left as is): ${pinned.join(', ')}` : ''}`];
}

/** installer-check: the installer contract (ORBIT-SP1.md "Every subsystem has an installer"), checked on a copy of a
 *  fixture repo: install twice (the second run changes nothing), nothing machine-specific written, status line shape. */
function installerCheck(name, fixture) {
  const tmp = join(realpathSync(process.env.TMPDIR || '/tmp'), `orbit-installer-check-${name}-${process.pid}`);
  rmSync(tmp, { recursive: true, force: true });
  cpSync(fixture, tmp, { recursive: true });
  if (!existsSync(join(tmp, '.git'))) { run('git', ['init', '-q'], { cwd: tmp }); run('git', ['add', '-A'], { cwd: tmp }); run('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'fixture'], { cwd: tmp }); }
  const snap = () => { const out = new Map(); const walk = d => { for (const e of readdirSync(d, { withFileTypes: true })) { const p = join(d, e.name); if (e.name === '.git') continue; e.isDirectory() ? walk(p) : out.set(relative(tmp, p), read(p)); } }; walk(tmp); return out; };
  const self = fileURLToPath(import.meta.url);
  const once = () => run('node', [self, 'install', tmp, '--only', name]);
  const before = snap(), r1 = once(), a = snap(), r2 = once(), b = snap();
  const changed = [...new Set([...a.keys(), ...b.keys()])].filter(k => a.get(k) !== b.get(k) && !/\.orbit-sp1\.json$|orbit-SP1\.json$/.test(k));
  const home = homedir();
  const machine = [...b].filter(([k, t]) => t.includes(home) && !k.endsWith('.log')).map(([k]) => k);
  const st = run('node', [self, 'status', tmp]).stdout.split('\n').find(l => l.startsWith('  ') && l.trim().startsWith(name)) || '';
  const rows = [
    ['install exits 0 (twice)', r1.status === 0 && r2.status === 0, (r1.stderr || r2.stderr).trim().split('\n').slice(-1)[0] || ''],
    ['idempotent: 2nd install changes nothing', changed.length === 0, changed.slice(0, 8).join(', ')],
    ['nothing machine-specific written', machine.length === 0, machine.slice(0, 8).join(', ')],
    ['status row: <name> <state> next: <step>', /^\s*\S+\s+(not installed|installed|online|host|skipped)\b.*next: \S/.test(st), st.trim()],
    ['installed something (metadata files written)', b.size > before.size, `${b.size - before.size} files`],
    ['never touches the repo\'s own files', [...before.keys()].every(k => PACK_OWNED.test(k) || before.get(k) === b.get(k)), [...before.keys()].filter(k => !PACK_OWNED.test(k) && before.get(k) !== b.get(k)).slice(0, 8).join(', ')],
  ];
  for (const [what, ok, note] of rows) console.log(`${ok ? 'PASS' : 'FAIL'}  ${what}${note ? `  (${note})` : ''}`);
  rmSync(tmp, { recursive: true, force: true });
  process.exit(rows.every(r => r[1]) ? 0 : 1);
}

if (cmd === 'skills-sync') skillsSync();
else if (cmd === 'sync') sync();
else if (cmd === 'installer-check' && target) installerCheck(target, resolve(argv.filter(x => !x.startsWith('--'))[2] ?? '.'));
else if (cmd === 'install' && target) install(resolve(target));
else if (cmd === 'status') status(resolve(target ?? '.'));
else if (cmd === 'compare' && target) compare(resolve(target));
else if (cmd === 'sim') process.exit(spawnSync('node', [join(HOME, 'bin', 'sim.mjs'), ...argv.slice(1)], { stdio: 'inherit' }).status ?? 1);
else if (cmd === 'cloud') process.exit(spawnSync('node', [join(HOME, 'bin', 'cloud.mjs'), ...argv.slice(1)], { stdio: 'inherit' }).status ?? 1);
else if (cmd === 'voyager') process.exit(spawnSync(join(HOME, 'voyager', 'bin', 'voyager'), argv.slice(1), { stdio: 'inherit' }).status ?? 1);
else if (cmd === 'gate') process.exit(spawnSync(join(HOME, 'gate', 'bin', 'gate'), argv.slice(1), { stdio: 'inherit' }).status ?? 1);
else if (cmd && !['install', 'compare', 'help', 'skills-sync', 'installer-check', 'sim', 'sync'].includes(cmd)) {
  // Engine passthrough: the nearest .orbit/orbit.toml up from here is the repo's config.
  let d = process.cwd();
  while (!existsSync(join(d, '.orbit', 'orbit.toml')) && dirname(d) !== d) d = dirname(d);
  const config = join(d, '.orbit', 'orbit.toml');
  if (!existsSync(config)) { console.error('orbit: no .orbit/orbit.toml in this directory or above (install: orbit install <repo>)'); process.exit(1); }
  const r = spawnSync(join(HOME, 'orbit', 'bin', 'orbit'), argv, { stdio: 'inherit', env: { ...process.env, ORBIT_CONFIG: config } });
  process.exit(r.status ?? 1);
} else { console.log(read(fileURLToPath(import.meta.url)).split('\n').filter(l => /^\/\/ {1,3}(orbit|No dep)/.test(l)).map(l => l.slice(3)).join('\n')); process.exit(cmd ? 1 : 0); }
