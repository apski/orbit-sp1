// L1 bootstrap: what Spectra needs from this repo, detected with evidence. Same model as NavLab's profile.py:
// each section is `detected` (a guess with evidence), `missing` (with the question to ask) or `confirmed` (by the user;
// never overwritten). The spectra-bootstrap skill shows detected values once, fixes and confirms them, and fills the gaps.
// Writes <repo>/.spectra/profile.json.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { basename, join, relative } from 'node:path';
import * as zlib from 'node:zlib';

const SKIP = new Set(['node_modules', '.git', 'Pods', 'build', 'android', '.spectra', 'dist', 'DerivedData', '.expo', '.cache', 'vendor']);
const STYLING = ['nativewind', 'twrnc', 'styled-components', '@emotion/native', 'tamagui', '@tamagui/core', '@shopify/restyle',
  'react-native-unistyles', 'dripsy', 'react-native-paper', '@gluestack-ui/themed', 'native-base'];

function walk(dir, exts, out = [], limit = 8000) {
  let entries = [];
  try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    if (out.length >= limit) break;
    if (e.isDirectory()) { if (!SKIP.has(e.name) && !e.name.startsWith('.')) walk(join(dir, e.name), exts, out, limit); }
    else if (exts.some(x => e.name.endsWith(x))) out.push(join(dir, e.name));
  }
  return out;
}
const read = p => { try { return readFileSync(p, 'utf8'); } catch { return ''; } };
const json = p => { try { return JSON.parse(read(p)); } catch { return null; } };

export const ASK = {
  jest: 'Which jest config runs the app\'s component tests (or should Spectra add a dedicated one under .spectra/jest)?',
  screens: 'Which screen should be converted first (component name and file)?',
  designSystem: 'Where do the shared UI components live (folders or packages)?',
  native: 'Where should generated SwiftUI go: an existing iOS app (which target/folder) or a new one?',
  figmaToken: 'Add a Figma personal access token as FIGMA_TOKEN=... in .spectra/secrets.env or ~/.spectra/secrets.env (never in chat).',
  simulator: 'Boot an iPhone simulator (or name one) for verify.',
};
export const NEEDS = {  // step -> [required, optional]
  'capture-jest': [['rn', 'jest'], ['screens', 'styling']],
  'capture-runtime': [['rn', 'native'], ['metro', 'simulator']],
  figma: [['figmaToken'], ['figma', 'fonts']],
  gen: [[], ['designSystem', 'native', 'fonts']],
  verify: [['xcode', 'simulator'], ['native']],
};

export function scan(repo) {
  const pkg = json(join(repo, 'package.json')) ?? {};
  const deps = { ...pkg.dependencies, ...pkg.devDependencies };
  const src = walk(repo, ['.tsx', '.ts', '.jsx', '.js']).filter(f => !/\.(test|spec)\.|__tests__|\.d\.ts$/.test(f));
  const P = {};

  const iosProps = json(join(repo, 'ios', 'Podfile.properties.json')) ?? {};
  const rnv = deps['react-native'];
  const minor = Number(/\d+\.(\d+)/.exec(rnv ?? '')?.[1] ?? 0);
  P.rn = { value: rnv ? { version: rnv, expo: deps.expo ?? null, newArch: iosProps.newArchEnabled === 'true' || iosProps.newArchEnabled === true || minor >= 76 } : null,
    evidence: rnv ? ['package.json', ...(iosProps.newArchEnabled ? ['ios/Podfile.properties.json'] : [`RN 0.${minor} default`])] : [] };

  const styled = STYLING.filter(p => deps[p]);
  const sheets = src.filter(f => read(f).includes('StyleSheet.create')).length;
  P.styling = { value: { libraries: styled, styleSheetFiles: sheets },
    evidence: [...styled.map(p => `package ${p}`), `${sheets} files call StyleSheet.create`],
    note: styled.includes('nativewind') ? 'NativeWind: jest captures need react-native-css-interop\'s jest setup; runtime capture works as-is' : undefined };

  const jestCfg = ['jest.config.js', 'jest.config.cjs', 'jest.config.ts', 'jest.config.mjs'].find(f => existsSync(join(repo, f))) ?? (pkg.jest ? 'package.json#jest' : null);
  const renderer = deps['react-test-renderer'] ?? null, rntl = deps['@testing-library/react-native'] ?? null;
  P.jest = { value: jestCfg && deps.jest ? { config: jestCfg, preset: /preset:\s*['"]([^'"]+)/.exec(read(join(repo, jestCfg)))?.[1] ?? pkg.jest?.preset ?? null,
    renderer, testingLibrary: rntl, harness: existsSync(join(repo, '.spectra', 'jest', 'jest.config.cjs')) } : null,
    evidence: jestCfg ? [jestCfg, renderer ? `react-test-renderer ${renderer}` : 'no react-test-renderer (needed)'] : [] };

  // Screens: exported components named *Screen, plus components registered with a navigator.
  const screens = new Map();
  for (const f of src) {
    const s = read(f);
    for (const m of s.matchAll(/export\s+(?:default\s+)?(?:function|const)\s+([A-Z]\w*Screen)\b/g)) screens.set(m[1], relative(repo, f));
    for (const m of s.matchAll(/component=\{\s*([A-Z]\w*)\s*\}/g)) if (!screens.has(m[1])) screens.set(m[1], `${relative(repo, f)} (navigator)`);
  }
  P.screens = { value: [...screens].slice(0, 40).map(([name, file]) => ({ name, file })), evidence: [`${screens.size} candidates`] };

  // Design system: NavLab's confirmed/detected value when the pack is installed, else folders many files render from.
  const navlab = json(join(repo, 'navlab.config.json'))?.profile?.designSystem;
  let ds = navlab?.value ?? [];
  if (!ds.length) {
    const dirs = {};
    for (const f of src) { const d = relative(repo, f).split('/').slice(0, -1); const i = d.findIndex(x => /^(components|design-?system|ui|atoms|primitives)$/i.test(x)); if (i >= 0) { const k = d.slice(0, i + 1).join('/'); dirs[k] = (dirs[k] ?? 0) + 1; } }
    ds = Object.entries(dirs).filter(([, n]) => n >= 5).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([d]) => d);
  }
  P.designSystem = { value: ds, evidence: navlab ? ['navlab.config.json profile'] : ds.map(d => `${d}/ (component folder)`) };

  const iosDir = join(repo, 'ios');
  const swift = existsSync(iosDir) ? walk(iosDir, ['.swift']) : [];
  const pbx = existsSync(iosDir) ? walk(iosDir, ['project.pbxproj'], [], 20) : [];
  const targets = pbx.flatMap(p => [...read(p).matchAll(/IPHONEOS_DEPLOYMENT_TARGET = ([\d.]+)/g)].map(m => Number(m[1])));
  P.native = { value: existsSync(iosDir) ? { ios: 'ios', projects: pbx.map(p => relative(repo, p).replace('/project.pbxproj', '')),
    swiftFiles: swift.length, swiftUIFiles: swift.filter(f => read(f).includes('import SwiftUI')).length,
    minIOS: targets.length ? Math.min(...targets) : null } : null,
    evidence: existsSync(iosDir) ? [`${swift.length} Swift files`, `${pbx.length} Xcode projects`] : [] };
  if (P.native.value?.minIOS && P.native.value.minIOS < 26) P.native.note = 'generated text uses .lineHeight (iOS 26); older targets need a lineSpacing fallback';

  const fonts = walk(repo, ['.ttf', '.otf'], [], 200).map(f => relative(repo, f));
  P.fonts = { value: fonts.map(f => basename(f).replace(/\.(ttf|otf)$/, '')), evidence: fonts.slice(0, 6) };

  const figmaKeys = new Set();
  for (const f of walk(repo, ['.tsx', '.ts', '.js', '.md', '.json'], [], 8000)) for (const m of read(f).matchAll(/figma\.com\/(?:design|file|proto)\/([A-Za-z0-9]{10,})/g)) figmaKeys.add(m[1]);
  const codeConnect = walk(repo, ['.figma.tsx', '.figma.ts'], [], 500).map(f => relative(repo, f));
  P.figma = { value: { files: [...figmaKeys], codeConnect: codeConnect.length, config: existsSync(join(repo, 'figma.config.json')) },
    evidence: [`${figmaKeys.size} Figma file links in the repo`, `${codeConnect.length} Code Connect files`] };
  const tokenSet = Boolean(process.env.FIGMA_TOKEN || process.env.FIGMA_ACCESS_TOKEN
    || [join(repo, '.spectra', 'secrets.env'), join(process.env.HOME ?? '', '.spectra', 'secrets.env')].some(p => /FIGMA_(ACCESS_)?TOKEN\s*=/.test(read(p))));
  P.figmaToken = { value: tokenSet || null, evidence: tokenSet ? ['token found (value not read into the profile)'] : [] };

  const port = /--port[ =](\d+)/.exec(Object.values(pkg.scripts ?? {}).join(' '))?.[1];
  P.metro = { value: { port: Number(port ?? 8081) }, evidence: [port ? 'package.json scripts --port' : 'default 8081'] };

  let xcode = null, sims = [];
  try { xcode = execFileSync('xcodebuild', ['-version'], { stdio: ['ignore', 'pipe', 'ignore'] }).toString().split('\n')[0]; } catch {}
  try { sims = Object.values(JSON.parse(execFileSync('xcrun', ['simctl', 'list', 'devices', 'booted', '-j']).toString()).devices).flat().filter(d => /iPhone/.test(d.name)); } catch {}
  P.xcode = { value: xcode, evidence: xcode ? [xcode] : [] };
  P.simulator = { value: sims.length ? sims.map(s => ({ name: s.name, udid: s.udid })) : null, evidence: sims.map(s => s.name),
    note: sims.length > 1 ? 'several booted: confirm which one Spectra may use (others may belong to other sessions)' : undefined };
  P.node = { value: { version: process.version, zstd: typeof zlib.zstdCompressSync === 'function' }, evidence: [process.version] };
  return P;
}

const profilePath = repo => join(repo, '.spectra', 'profile.json');

export function detect(repo) {
  const p = profilePath(repo);
  const prof = json(p) ?? {};
  for (const [k, v] of Object.entries(scan(repo))) {
    if (prof[k]?.status === 'confirmed') continue;
    const present = v.value !== null && !(Array.isArray(v.value) && !v.value.length);
    prof[k] = { ...v, status: present ? 'detected' : 'missing', ...(present ? {} : ASK[k] ? { ask: ASK[k] } : {}) };
  }
  mkdirSync(join(repo, '.spectra'), { recursive: true });
  writeFileSync(p, JSON.stringify(prof, null, 1));
  console.log(`spectra profile ${repo}:`);
  for (const [k, v] of Object.entries(prof)) {
    const val = JSON.stringify(v.value) ?? 'null';
    console.log(`  ${v.status.padEnd(9)} ${k.padEnd(12)} ${val.length > 96 ? val.slice(0, 93) + '...' : val}${v.note ? `\n${' '.repeat(24)}note: ${v.note}` : ''}`);
  }
  console.log('-> .spectra/profile.json. Confirm or correct detected values with the user, then `spectra confirm <section>`.');
}

export function check(repo, step) {
  const prof = json(profilePath(repo));
  if (!prof) { console.log('no profile yet: spectra detect'); return 1; }
  const steps = step ? [step] : Object.keys(NEEDS);
  let bad = 0;
  for (const s of steps) {
    const [req, opt] = NEEDS[s] ?? die(`unknown step ${s} (${Object.keys(NEEDS).join(', ')})`);
    const missing = req.filter(k => prof[k]?.status === 'missing' || !prof[k]);
    console.log(`${s}: ${missing.length ? 'NOT READY' : 'ready'}`);
    for (const k of [...req, ...opt]) {
      const st = prof[k]?.status ?? 'missing';
      console.log(`  ${st.padEnd(9)} ${k.padEnd(12)} ${req.includes(k) ? 'required' : 'optional'}${st === 'missing' && ASK[k] ? `  ask: ${ASK[k]}` : ''}`);
    }
    bad += missing.length;
  }
  return bad ? 1 : 0;
}

export function confirm(repo, section) {
  const prof = json(profilePath(repo)) ?? {};
  if (!section) die('usage: spectra confirm <section>');
  prof[section] = { ...(prof[section] ?? { value: null, evidence: [] }), status: 'confirmed' };
  writeFileSync(profilePath(repo), JSON.stringify(prof, null, 1));
  console.log(`${section}: confirmed`);
}

function die(m) { console.error(`spectra: ${m}`); process.exit(1); }
