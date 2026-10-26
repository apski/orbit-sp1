#!/usr/bin/env node
// rnfp: native fingerprint cache for React Native iOS simulator builds.
// Hashes only what goes into the native binary (ios/ sources, Podfile.lock, autolinked native deps, patches/,
// Xcode version, RCT_/USE_ env, config). Same hash = the native app is identical, so a new worktree or a JS-only
// change never recompiles: Debug installs the cached .app (JS comes from Metro), Release re-embeds a fresh Hermes
// bundle into a clone of the cached .app with RN's own react-native-xcode.sh and re-signs it ad hoc.
//
//   rnfp.mjs hash  [--config Debug|Release]          print the fingerprint
//   rnfp.mjs why   [--config ...]                     inputs that differ from the newest cached entry (why a miss)
//   rnfp.mjs build [--config ...] [--udid U] [--scheme S] [--no-store] [-- extra xcodebuild args]
//                                                     hit: reuse; miss: xcodebuild, then store. --udid installs.
//   rnfp.mjs ls                                       cached entries for this app
// Options: --root <rn project dir> (default cwd). Cache: $RNFP_CACHE or ~/.cache/rnfp (APFS clones, ~free).
// Simulator only: device builds need signing, build those with xcodebuild.
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, mkdirSync, rmSync, writeFileSync, statSync, mkdtempSync } from 'node:fs';
import { join, basename, resolve, relative } from 'node:path';
import { homedir, tmpdir } from 'node:os';

const args = process.argv.slice(2);
const dash = args.indexOf('--');
const own = dash < 0 ? args : args.slice(0, dash);
const extra = dash < 0 ? [] : args.slice(dash + 1);
const opt = (k, d) => (own.includes(`--${k}`) ? own[own.indexOf(`--${k}`) + 1] : d);
const ROOT = resolve(opt('root', '.'));
const IOS = join(ROOT, 'ios');
const CONFIG = opt('config', 'Debug');
const UDID = opt('udid');
const CACHE = process.env.RNFP_CACHE || join(homedir(), '.cache', 'rnfp');
const KEEP = 6; // ponytail: keep the newest 6 entries per app, mtime order; LRU by last hit if this thrashes
const sh = (c, a, o = {}) => execFileSync(c, a, { encoding: 'utf8', maxBuffer: 1 << 28, stdio: ['ignore', 'pipe', 'pipe'], ...o });
const run = (c, a, o = {}) => { const r = spawnSync(c, a, { stdio: 'inherit', ...o }); if (r.status) die(`${c} ${a.slice(0, 3).join(' ')} ... failed (${r.status})`); };
const die = m => { console.error(`rnfp: ${m}`); process.exit(1); };
const sha = b => createHash('sha256').update(b).digest('hex').slice(0, 16);

if (!existsSync(join(IOS, 'Podfile'))) die(`${IOS}/Podfile not found (pass --root <react native project>)`);
const proj = readdirSync(IOS).find(f => f.endsWith('.xcodeproj'));
const SCHEME = opt('scheme', proj && basename(proj, '.xcodeproj'));
const APPDIR = join(CACHE, SCHEME || 'app');

// git-visible files (tracked + untracked-not-ignored) under dir, minus build output and per-user state
function files(dir) {
  return sh('git', ['ls-files', '-co', '--exclude-standard', '-z', '.'], { cwd: dir }).split('\0')
    .filter(f => f && !/^(build[^/]*|Pods)\/|(^|\/)xcuserdata\/|\.xcode\.env\.local$/.test(f))
    .filter(f => statSync(join(dir, f), { throwIfNoEntry: false })?.isFile()).sort();
}

function inputs() {
  const m = {};
  for (const f of files(IOS)) m[`ios/${f}`] = sha(readFileSync(join(IOS, f)));
  if (existsSync(join(ROOT, 'patches'))) for (const f of files(join(ROOT, 'patches'))) m[`patches/${f}`] = sha(readFileSync(join(ROOT, 'patches', f)));
  const ver = d => JSON.parse(readFileSync(join(d, 'package.json'), 'utf8')).version;
  m['react-native'] = ver(join(ROOT, 'node_modules/react-native'));
  // the app's own TurboModule/Fabric specs are JS that codegen turns into native code
  const cg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')).codegenConfig;
  if (cg) m.codegenConfig = JSON.stringify(cg);
  if (cg?.jsSrcsDir) for (const f of files(join(ROOT, cg.jsSrcsDir))) m[`codegen ${cg.jsSrcsDir}/${f}`] = sha(readFileSync(join(ROOT, cg.jsSrcsDir, f)));
  // autolinking's own view of native deps: published ones by version, local ones by content
  const cfg = JSON.parse(sh('node', [join(ROOT, 'node_modules/react-native/cli.js'), 'config'], { cwd: ROOT }));
  for (const [name, d] of Object.entries(cfg.dependencies || {})) {
    if (!d.platforms?.ios) continue;
    if (d.root.includes('/node_modules/')) m[`dep ${name}`] = ver(d.root);
    else for (const f of files(d.root)) m[`dep ${relative(ROOT, d.root)}/${f}`] = sha(readFileSync(join(d.root, f)));
  }
  m.xcode = sh('xcodebuild', ['-version']).trim().replace(/\s+/g, ' ');
  m.build = JSON.stringify({ CONFIG, SCHEME, extra });
  for (const [k, v] of Object.entries(process.env)) if (/^(RCT_|USE_)/.test(k)) m[`env ${k}`] = v;
  return m;
}
const fpOf = m => sha(Object.keys(m).sort().map(k => `${k}\0${m[k]}`).join('\n'));
const entries = () => (existsSync(APPDIR) ? readdirSync(APPDIR) : [])
  .filter(e => existsSync(join(APPDIR, e, 'meta.json')))
  .map(e => ({ dir: join(APPDIR, e), meta: JSON.parse(readFileSync(join(APPDIR, e, 'meta.json'), 'utf8')) }))
  .sort((a, b) => b.meta.created.localeCompare(a.meta.created));
const plist = (p, k) => { try { return sh('plutil', ['-extract', k, 'raw', '-o', '-', p]).trim(); } catch { return ''; } };

// Release hit: clone the cached app, let RN's own bundle script write a fresh main.jsbundle (Hermes bytecode, assets)
// into it with the hermesc that built the cached engine, then re-sign ad hoc.
function rebundle(entry) {
  const out = mkdtempSync(join(tmpdir(), 'rnfp-'));
  run('cp', ['-cR', join(entry.dir, entry.meta.app), out]);
  const dst = join(out, entry.meta.app), info = join(dst, 'Info.plist');
  const env = {
    ...process.env, CONFIGURATION: CONFIG, PLATFORM_NAME: 'iphonesimulator', CONFIGURATION_BUILD_DIR: out,
    UNLOCALIZED_RESOURCES_FOLDER_PATH: entry.meta.app, DERIVED_FILE_DIR: join(out, 'derived'), PROJECT_DIR: IOS,
    PODFILE_DIR: IOS, PODS_ROOT: join(IOS, 'Pods'), REACT_NATIVE_PATH: join(ROOT, 'node_modules/react-native'),
    PRODUCT_SETTINGS_PATH: info, IPHONEOS_DEPLOYMENT_TARGET: plist(info, 'MinimumOSVersion'),
    TARGETED_DEVICE_FAMILY: [0, 1, 2].map(i => plist(info, `UIDeviceFamily.${i}`)).filter(Boolean).join(','),
  };
  if (existsSync(join(entry.dir, 'hermesc'))) env.HERMES_CLI_PATH = join(entry.dir, 'hermesc');
  const rn = join(ROOT, 'node_modules/react-native/scripts');
  const r = spawnSync('/bin/sh', [join(rn, 'xcode/with-environment.sh'), join(rn, 'react-native-xcode.sh')], { env, encoding: 'utf8', cwd: IOS });
  if (r.status) die(`bundle failed:\n${(r.stderr || '').split('\n').filter(l => !l.startsWith('+')).slice(-20).join('\n')}`);
  run('codesign', ['--force', '--sign', '-', '--timestamp=none', '--preserve-metadata=identifier,entitlements,flags', dst], { stdio: 'ignore' });
  return dst;
}

// Pods out of sync: clone them from a sibling worktree with the same Podfile.lock (APFS clone, no CocoaPods cache
// lock, which parallel pod installs serialize on), then regenerate codegen for this tree's specs. Else pod install.
function pods() {
  const lock = p => (existsSync(p) ? readFileSync(p, 'utf8') : '');
  const want = lock(join(IOS, 'Podfile.lock'));
  if (want && want === lock(join(IOS, 'Pods/Manifest.lock'))) return;
  const top = sh('git', ['rev-parse', '--show-toplevel'], { cwd: IOS }).trim();
  const rel = relative(top, IOS);
  const wts = sh('git', ['worktree', 'list', '--porcelain'], { cwd: IOS }).split('\n').filter(l => l.startsWith('worktree ')).map(l => l.slice(9));
  const src = wts.map(w => join(w, rel)).find(d => d !== IOS && want && lock(join(d, 'Pods/Manifest.lock')) === want);
  // the project's pinned CocoaPods (Gemfile): another version rewrites every Podfile.lock checksum, i.e. a new fingerprint
  if (!src) return existsSync(join(ROOT, 'Gemfile')) ? run('bundle', ['exec', 'pod', 'install'], { cwd: IOS }) : run('pod', ['install'], { cwd: IOS });
  rmSync(join(IOS, 'Pods'), { recursive: true, force: true });
  run('cp', ['-cR', join(src, 'Pods'), IOS]);
  mkdirSync(join(IOS, 'build'), { recursive: true });
  // ponytail: the pod-install outputs RN 0.88 writes under ios/build; re-check this list on RN upgrades
  for (const d of ['rncore-facades', 'rndeps-facades']) if (existsSync(join(src, 'build', d))) {
    rmSync(join(IOS, 'build', d), { recursive: true, force: true });
    run('cp', ['-cR', join(src, 'build', d), join(IOS, 'build')]);
  }
  run('node', [join(ROOT, 'node_modules/react-native/scripts/generate-codegen-artifacts.js'), '-p', ROOT, '-o', IOS, '-t', 'ios'], { stdio: 'ignore' });
  console.error(`rnfp: cloned Pods from ${src}`);
}

function build(m, fp) {
  if (!SCHEME) die(`no .xcodeproj in ${IOS}`);
  // one build per worktree: two xcodebuilds on one DerivedData corrupt each other's products
  const lockDir = join(IOS, 'build', 'rnfp.lock');
  mkdirSync(join(IOS, 'build'), { recursive: true });
  try { mkdirSync(lockDir); } catch { die(`another rnfp build is running in ${ROOT} (stale? rm -r ${lockDir})`); }
  process.on('exit', () => rmSync(lockDir, { recursive: true, force: true }));
  pods();
  const ws = readdirSync(IOS).find(f => f.endsWith('.xcworkspace'));
  if (!ws) die(`no .xcworkspace in ${IOS} after pod install`);
  const dd = join(IOS, 'build/rnfp');
  run('xcodebuild', ['-workspace', ws, '-scheme', SCHEME, '-configuration', CONFIG, '-sdk', 'iphonesimulator',
    '-destination', 'generic/platform=iOS Simulator', '-derivedDataPath', dd, 'ARCHS=arm64',
    'COMPILATION_CACHE_ENABLE_CACHING=YES', '-quiet', ...extra, 'build'], { cwd: IOS });
  const prod = join(dd, 'Build/Products', `${CONFIG}-iphonesimulator`);
  const app = readdirSync(prod).filter(f => f.endsWith('.app')).sort((a, b) => statSync(join(prod, b)).mtimeMs - statSync(join(prod, a)).mtimeMs)[0];
  if (!app || !plist(join(prod, app, 'Info.plist'), 'CFBundleIdentifier')) die(`no complete .app in ${prod}`);
  if (own.includes('--no-store')) return join(prod, app);
  const dir = join(APPDIR, fp);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  run('cp', ['-cR', join(prod, app), dir]);
  const hermesc = join(IOS, 'Pods/hermes-engine/destroot/bin/hermesc');
  if (existsSync(hermesc)) run('cp', ['-c', hermesc, dir]);
  writeFileSync(join(dir, 'meta.json'), JSON.stringify({ fp, app, config: CONFIG, root: ROOT, created: new Date().toISOString(), inputs: m }, null, 1));
  for (const old of entries().slice(KEEP)) rmSync(old.dir, { recursive: true, force: true });
  return join(dir, app);
}

const cmd = own[0];
const t0 = Date.now();
if (cmd === 'ls') {
  for (const e of entries()) console.log(`${e.meta.fp}  ${e.meta.config.padEnd(8)}${e.meta.created}  ${e.meta.root}`);
} else if (cmd === 'hash') {
  console.log(fpOf(inputs()));
} else if (cmd === 'why') {
  const m = inputs(), last = entries().find(e => e.meta.config === CONFIG);
  if (!last) die(`nothing cached for ${SCHEME} ${CONFIG}`);
  if (fpOf(m) === last.meta.fp) { console.log(`hit: matches ${last.meta.fp}`); process.exit(0); }
  const keys = [...new Set([...Object.keys(m), ...Object.keys(last.meta.inputs)])].sort();
  const diff = keys.filter(k => m[k] !== last.meta.inputs[k]);
  for (const k of diff.slice(0, 30)) console.log(`${!(k in m) ? 'removed' : !(k in last.meta.inputs) ? 'added  ' : 'changed'}  ${k}`);
  if (diff.length > 30) console.log(`... ${diff.length - 30} more`);
} else if (cmd === 'build') {
  const m = inputs(), fp = fpOf(m);
  const hit = entries().find(e => e.meta.fp === fp && existsSync(join(e.dir, e.meta.app)));
  let app, state;
  if (!hit) { app = build(m, fp); state = 'miss'; }
  else if (CONFIG === 'Debug') { app = join(hit.dir, hit.meta.app); state = 'hit'; }
  else { app = rebundle(hit); state = 'rebundle'; }
  if (UDID) run('xcrun', ['simctl', 'install', UDID, app]);
  console.log(`rnfp  ${state}  ${fp}  ${((Date.now() - t0) / 1000).toFixed(1)}s  ${app}${UDID ? `  installed on ${UDID}` : ''}`);
} else {
  die('usage: rnfp.mjs hash|why|build|ls [--config Debug|Release] [--udid U] [--root dir] [--scheme S] [-- xcodebuild args]');
}
