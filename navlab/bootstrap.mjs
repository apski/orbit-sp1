#!/usr/bin/env node
// NavLab bootstrap: point the lab at an existing React Native + React Navigation repo.
//
//   node <navlab>/bootstrap.mjs <repo> [--udid <sim or phone>] [--no-skills]
//
// Writes metadata only (never app code): navlab.config.json, navlab.adapter.json (first scan + learned facts),
// .navlab/navscan.json, the navlab skills into <repo>/.claude/skills/, a navlab block in <repo>/AGENTS.md and
// `.navlab/traces/` in .gitignore. Re-runnable: keeps your edits to config/adapter, refreshes what it detects.
// Wiring navprobe into the app is the navlab-install skill's job (a reviewed code change).
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HOME = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const opt = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const REPO = path.resolve(argv.find((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--')) || '.');
const j = (f) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return null; } };
const exists = (p) => fs.existsSync(path.join(REPO, p));
const say = (s) => console.log(s);

const pkg = j(path.join(REPO, 'package.json'));
if (!pkg) { console.error(`no package.json in ${REPO}`); process.exit(1); }
const deps = { ...pkg.dependencies, ...pkg.devDependencies };
const has = (d) => deps[d];
if (!has('react-native')) { console.error('not a React Native app (no react-native dependency)'); process.exit(1); }

// ---------- detect ----------
const libs = (names) => names.filter(has).map((n) => `${n}@${deps[n]}`);
const detected = {
  reactNative: deps['react-native'],
  expo: has('expo') || null,
  expoRouter: has('expo-router') || null,
  navigation: libs(['@react-navigation/native', '@react-navigation/native-stack', '@react-navigation/stack', '@react-navigation/bottom-tabs',
    '@react-navigation/drawer', '@react-navigation/material-top-tabs', 'react-native-screens', 'react-native-navigation']),
  state: libs(['redux', '@reduxjs/toolkit', 'react-redux', 'zustand', 'mobx', 'mobx-react', 'mobx-state-tree', 'jotai', 'recoil', 'valtio', 'xstate']),
  network: libs(['axios', '@tanstack/react-query', 'react-query', '@apollo/client', 'urql', 'relay-runtime', 'react-relay', 'swr', 'ky']),
  lists: libs(['@shopify/flash-list', 'recyclerlistview', 'react-native-reanimated', 'react-native-gesture-handler']),
  typescript: exists('tsconfig.json'),
  packageManager: exists('yarn.lock') ? 'yarn' : exists('pnpm-lock.yaml') ? 'pnpm' : exists('bun.lockb') ? 'bun' : 'npm',
  entry: pkg.main || ['index.js', 'index.ts', 'index.tsx'].find(exists) || null,
};
if (detected.navigation.some((n) => n.startsWith('react-native-navigation@'))) say('note: react-native-navigation (Wix) detected; navscan/navprobe target React Navigation only');

// iOS: workspace, scheme, bundle id, process name
const ios = {};
if (exists('ios')) {
  const iosDir = path.join(REPO, 'ios');
  const ws = fs.readdirSync(iosDir).find((f) => f.endsWith('.xcworkspace'));
  const proj = fs.readdirSync(iosDir).find((f) => f.endsWith('.xcodeproj'));
  ios.workspace = ws ? `ios/${ws}` : null;
  ios.project = proj ? `ios/${proj}` : null;
  ios.scheme = (ws || proj || '').replace(/\.(xcworkspace|xcodeproj)$/, '') || null;
  const pbx = proj && path.join(iosDir, proj, 'project.pbxproj');
  if (pbx && fs.existsSync(pbx)) {
    const src = fs.readFileSync(pbx, 'utf8');
    const ids = [...src.matchAll(/PRODUCT_BUNDLE_IDENTIFIER = "?([\w.$()-]+)"?;/g)].map((m) => m[1]).filter((x) => !/Tests?$|\$\(/.test(x));
    ios.bundleId = ids.sort((a, b) => a.length - b.length)[0] || null;
    const names = [...src.matchAll(/PRODUCT_NAME = "?([^";]+)"?;/g)].map((m) => m[1]).filter((x) => !x.includes('$') && !/Tests?$/.test(x));
    ios.process = names[0] || ios.scheme;
  }
}
const appJson = j(path.join(REPO, 'app.json'));
ios.bundleId ||= appJson?.expo?.ios?.bundleIdentifier || null;
ios.process ||= appJson?.expo?.name || appJson?.name || null;
// app.config.js/ts (Expo CNG, no ios/ yet): evaluate with the Expo CLI when installed, else read the literals (gap g0035)
const appCfg = ['app.config.js', 'app.config.ts'].map((f) => path.join(REPO, f)).find((f) => fs.existsSync(f));
if (appCfg && (!ios.bundleId || !ios.process)) {
  let ex = null;
  try { ex = JSON.parse(execFileSync('npx', ['--no-install', 'expo', 'config', '--json', '--type', 'public'], { cwd: REPO, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 60000 })); } catch {}
  // ponytail: first literal wins (the app's own ios block precedes extension targets in practice); --type public is exact
  const src = fs.readFileSync(appCfg, 'utf8');
  ios.bundleId ||= ex?.ios?.bundleIdentifier || src.match(/bundleIdentifier:\s*['"]([\w.-]+)['"]/)?.[1] || null;
  ios.process ||= ex?.name || src.match(/\bname:\s*['"]([^'"]+)['"]/)?.[1] || null;
}

// where NavigationContainer lives (probe wiring point)
let containers = [];
try {
  containers = execFileSync('grep', ['-rlE', '<NavigationContainer|createStaticNavigation|useNavigationContainerRef', '--include=*.tsx', '--include=*.ts', '--include=*.jsx', '--include=*.js',
    '--exclude-dir=node_modules', '--exclude-dir=ios', '--exclude-dir=android', '--exclude-dir=.git', REPO], { encoding: 'utf8' })
    .trim().split('\n').filter(Boolean).map((f) => path.relative(REPO, f)).filter((f) => !/test|mock|stories/i.test(f));
} catch {}

// ---------- config (merge: user edits win) ----------
const cfgPath = path.join(REPO, 'navlab.config.json');
const prev = j(cfgPath) || {};
const cfg = {
  navlabHome: HOME,
  bundleId: ios.bundleId,
  process: ios.process,
  scheme: ios.scheme,
  workspace: ios.workspace || ios.project,
  udid: opt('--udid', null),
  model: path.relative(REPO, path.join(HOME, 'baseline', 'sim-iphone17pro-ios26.4', 'model.json')),
  baseline: path.relative(REPO, path.join(HOME, 'baseline', 'sim-iphone17pro-ios26.4')),
  journeys: 'navlab.journeys.json',
  journeyLimit: 90,
  ...prev,
  detected,
  navigationContainers: containers,
};
fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2) + '\n');
if (!fs.existsSync(path.join(REPO, cfg.journeys)))
  fs.writeFileSync(path.join(REPO, cfg.journeys), JSON.stringify({ _doc: 'name -> steps: {navigate|push: Route, params}, {goBack: true}, {popToTop: true}, {wait: ms}. Run: journey.py <name>', smoke: [{ wait: 500 }] }, null, 2) + '\n');

// ---------- first scan (learns into the adapter) ----------
fs.mkdirSync(path.join(REPO, '.navlab'), { recursive: true });
let scan = '';
try {
  scan = execFileSync('node', [path.join(HOME, 'scan', 'navscan.mjs'), REPO, '--learn', '--json', path.join(REPO, '.navlab', 'navscan.json'), '--top', '8'], { encoding: 'utf8', maxBuffer: 64e6 });
} catch (e) { scan = (e.stdout || '') + (e.stderr || ''); }

// ---------- facts + repo profile (conventions every skill reads; never overwrites confirmed sections) ----------
let profile = '';
try {
  execFileSync('node', [path.join(HOME, 'arch', 'facts.mjs'), REPO], { encoding: 'utf8', maxBuffer: 64e6 });
  profile = execFileSync('python3', [path.join(HOME, 'arch', 'profile.py'), 'detect', REPO], { encoding: 'utf8', maxBuffer: 64e6 });
  execFileSync('node', [path.join(HOME, 'arch', 'facts.mjs'), REPO], { encoding: 'utf8', maxBuffer: 64e6 }); // with the detected conventions
} catch (e) { profile = (e.stdout || '') + (e.stderr || ''); }
// learning loop: what this repo showed the pack can't handle yet -> candidate gaps (AGENTS.md "Learning loop")
let gaps = '';
try { gaps = execFileSync('python3', [path.join(HOME, 'arch', 'gaps.py'), 'harvest', REPO], { encoding: 'utf8' }).trim(); } catch (e) { gaps = `gaps harvest failed: ${e.message}`; }

// ---------- skills + AGENTS.md block + gitignore ----------
if (!argv.includes('--no-skills')) {
  for (const s of fs.readdirSync(path.join(HOME, 'skills'))) {
    const dst = path.join(REPO, '.claude', 'skills', s);
    fs.mkdirSync(dst, { recursive: true });
    const src = fs.readFileSync(path.join(HOME, 'skills', s, 'SKILL.md'), 'utf8').replaceAll('$NAVLAB_HOME', HOME);
    fs.writeFileSync(path.join(dst, 'SKILL.md'), src);
  }
}
const block = `<!-- navlab:start (managed by navlab bootstrap; edit outside the markers) -->
## NavLab (navigation performance lab)
Rules, workflow and baseline: \`${HOME}/AGENTS.md\`. Config: \`navlab.config.json\`. Adapter: \`navlab.adapter.json\`.
Skills: \`navlab-install\` (wire probe/signposts/journeys; discover what else to instrument), \`navlab-audit\` (scan -> probe -> measure -> compare with baseline), \`design-afresh\` (boundaries from code + history; redesign a feature for its current scope; backtest candidates),
\`app-architecture-steward\` (whole-app program: \`arch/portfolio.py\` -> move per feature -> \`arch/ledger.py\` -> CI fitness functions),
\`rn-native-migration\` (RN/native seams, ROI, hosting patterns).
\`node ${path.relative(REPO, path.join(HOME, 'scan', 'navscan.mjs'))} .\` prints the navigator tree and the predicted cost of every stack.
<!-- navlab:end -->`;
const agentsPath = path.join(REPO, 'AGENTS.md');
const agents = fs.existsSync(agentsPath) ? fs.readFileSync(agentsPath, 'utf8') : '';
fs.writeFileSync(agentsPath, agents.includes('<!-- navlab:start') ? agents.replace(/<!-- navlab:start[\s\S]*?<!-- navlab:end -->/, block) : agents + (agents ? '\n\n' : '') + block + '\n');
const gi = path.join(REPO, '.gitignore');
const giSrc = fs.existsSync(gi) ? fs.readFileSync(gi, 'utf8') : '';
if (!giSrc.includes('.navlab/traces')) fs.appendFileSync(gi, `${giSrc.endsWith('\n') || !giSrc ? '' : '\n'}.navlab/traces/\n`);

// ---------- report ----------
say(`navlab bootstrap: ${REPO}`);
say(`  RN ${detected.reactNative}${detected.expo ? `, expo ${detected.expo}` : ''}${detected.expoRouter ? ', expo-router' : ''}; ${detected.navigation.join(', ') || 'no React Navigation found'}`);
say(`  state: ${detected.state.join(', ') || '-'}   network: ${detected.network.join(', ') || 'fetch only'}   lists/anim: ${detected.lists.join(', ') || '-'}`);
say(`  iOS: ${cfg.workspace || 'no ios/ dir (Expo managed? run expo prebuild)'}  scheme ${cfg.scheme}  bundle ${cfg.bundleId}  process ${cfg.process}`);
say(`  NavigationContainer: ${containers.join(', ') || 'not found (expo-router manages it, or a custom wrapper)'}`);
say(`  wrote navlab.config.json, navlab.adapter.json, ${cfg.journeys}, .navlab/navscan.json, AGENTS.md block${argv.includes('--no-skills') ? '' : ', .claude/skills/ (navlab-install, navlab-audit, design-afresh, app-architecture-steward, rn-native-migration)'}\n`);
say(scan.split('\n').slice(0, 60).join('\n'));
say('\n' + profile.trim());
say(gaps);
say(`\nnext: ask the agent to "install navlab": it confirms the detected profile with you once, then every skill is ready
(check one: python3 ${path.join(HOME, 'arch', 'profile.py')} check <skill>).`);
