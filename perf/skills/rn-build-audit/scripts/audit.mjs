#!/usr/bin/env node
// rn-build-audit: read-only scan of a React Native (or native iOS) repo for dev-loop build waste.
// Every finding names where it is and a LOCAL-ONLY fix (nothing committed, nothing reaching release builds).
//   node audit.mjs [repo] [--json]
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, relative, basename, dirname } from 'node:path';
import { homedir } from 'node:os';

const argv = process.argv.slice(2);
const ROOT = argv.find(a => !a.startsWith('--')) || '.';
const JSON_OUT = argv.includes('--json');
const SKILLS = join(dirname(new URL(import.meta.url).pathname), '..', '..');
const OVERLAY = join(SKILLS, 'xcode-build-speed', 'fast-sim.xcconfig');
const RNFP = join(SKILLS, 'rn-dev-loop', 'scripts', 'rnfp.mjs');
const read = p => (existsSync(p) ? readFileSync(p, 'utf8') : '');
const git = (...a) => { try { return execFileSync('git', a, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch { return ''; } };
const rel = p => relative(ROOT, p) || '.';
// files matching a suffix: git's view when ROOT is a repo, else a shallow walk (no node_modules/Pods/build)
const SKIP = /^(node_modules|Pods|build|DerivedData|\.git|vendor)$/;
function list(suffix, depth = 5, dir = ROOT) {
  const out = git('ls-files', '-co', '--exclude-standard', `*${suffix}`).split('\n').filter(Boolean);
  if (out.length || git('rev-parse', '--git-dir')) return out;
  const walk = (d, n) => n < 0 ? [] : readdirSync(d, { withFileTypes: true }).flatMap(e =>
    SKIP.test(e.name) ? [] : join(d, e.name).endsWith(suffix) ? [relative(ROOT, join(d, e.name))] : e.isDirectory() ? walk(join(d, e.name), n - 1) : []);
  return walk(dir, depth);
}
const lineOf = (text, re) => { const i = text.search(re); return i < 0 ? 0 : text.slice(0, i).split('\n').length; };
const findings = [];
const add = (win, id, where, why, fix) => findings.push({ win, id, where, why, fix });

// ---- React Native facts
const pkg = JSON.parse(read(join(ROOT, 'package.json')) || '{}');
const rnVer = JSON.parse(read(join(ROOT, 'node_modules/react-native/package.json')) || '{}').version
  || (pkg.dependencies?.['react-native'] || '').replace(/^[^\d]*/, '');
const rnMinor = Number((rnVer || '0.0').split('.')[1]) || 0;
const isRN = Boolean(rnVer);
const IOS = join(ROOT, 'ios');

// ---- Xcode projects (RN: ios/; native repos: wherever git has one)
const projects = list('.xcodeproj/project.pbxproj')
  .filter(p => p && !/(^|\/)(Pods|node_modules|vendor|fixtures?|tests?)\//.test(p)).map(p => join(ROOT, p));

for (const pbx of projects) {
  const s = read(pbx);
  const obj = id => { const m = s.match(new RegExp(`\\n\\t\\t${id} /\\*[^*]*\\*/ = \\{([\\s\\S]*?)\\n\\t\\t\\};`)); return m ? m[1] : ''; };
  const ids = (body, key) => [...(body.match(new RegExp(`${key} = \\(([\\s\\S]*?)\\);`))?.[1] || '').matchAll(/([0-9A-F]{24})/g)].map(m => m[1]);
  const val = (body, key) => body.match(new RegExp(`\\b${key} = ("(?:[^"\\\\]|\\\\.)*"|[^;]+);`))?.[1];
  const targets = [...s.matchAll(/\n\t\t([0-9A-F]{24}) \/\* ([^*]+) \*\/ = \{\n\t\t\tisa = PBXNativeTarget;/g)].map(m => ({ id: m[1], name: m[2], body: obj(m[1]) }));
  let anyCache = false;
  for (const t of targets.filter(t => /product-type\.application"/.test(t.body))) {
    const list = (val(t.body, 'buildConfigurationList') || '').slice(0, 24);
    for (const cid of ids(obj(list), 'buildConfigurations')) {
      const c = obj(cid), name = val(c, 'name');
      if (/COMPILATION_CACHE_ENABLE_CACHING = YES/.test(c)) anyCache = true;
      if (/debug/i.test(name) && /DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym"/.test(c))
        add('high', 'ios-debug-dsym', `${rel(pbx)}:${lineOf(s, new RegExp(`${cid} /\\*[^*]*\\*/ = \\{`))} (${t.name} ${name})`,
          'dsymutil runs on every Debug build and is never cached',
          `xcodebuild -xcconfig ${OVERLAY} (sets dwarf), or commit DEBUG_INFORMATION_FORMAT = dwarf for Debug only`);
    }
    for (const pid of ids(t.body, 'buildPhases')) {
      const p = obj(pid);
      if (!/isa = PBXShellScriptBuildPhase/.test(p)) continue;
      const pname = (val(p, 'name') || 'Run Script').replace(/"/g, '');
      let script = ''; try { script = JSON.parse(val(p, 'shellScript') || '""'); } catch { script = val(p, 'shellScript') || ''; }
      const where = `${rel(pbx)}:${lineOf(s, new RegExp(`${pid} /\\*[^*]*\\*/ = \\{`))} (${t.name}: ${pname})`;
      const gated = /CONFIGURATION|SKIP_BUNDLING|exit 0/.test(script.split('\n').slice(0, 6).join('\n'));
      if (/sentry-cli|sentry-xcode|upload-symbols|crashlytics|bugsnag|datadog-ci|firebase .*upload|dsym.*upload/i.test(script) && !gated)
        add('high', 'ios-upload-in-debug', where, 'uploads symbols/sourcemaps on every build, Debug included (network + noise)',
          'local: `export SENTRY_DISABLE_AUTO_UPLOAD=true` in ios/.xcode.env.local for RN Sentry phases; or gate the phase: `[ "$CONFIGURATION" = "Release" ] || exit 0`');
      const noOutputs = !/outputPaths = \(\s*"/.test(p) && !/outputFileListPaths = \(\s*"/.test(p);
      const trivial = script.split('\n').every(l => /^\s*(#.*|echo .*)?$/.test(l));
      if (noOutputs && !gated && !trivial && !/react-native-xcode\.sh|Check Pods Manifest/.test(script + pname) && !/alwaysOutOfDate = 1/.test(p))
        add('med', 'ios-script-every-build', where, 'no output files declared, so Xcode runs it on every build',
          'declare outputPaths (or an output file list) so it is skipped when inputs are unchanged; local: wrap with `[ -n "$LOCAL_FAST" ] && exit 0` and set LOCAL_FAST in ios/.xcode.env.local');
      if (/FORCE_BUNDLING/.test(script))
        add('med', 'ios-force-bundling', where, 'bundles JS in Debug even on the simulator, where Metro serves it', 'drop FORCE_BUNDLING for simulator Debug');
    }
  }
  if (!anyCache)
    add('high', 'ios-compilation-cache-off', rel(pbx), 'every worktree / clean build recompiles everything (Xcode 26+ cache is opt-in)',
      `xcodebuild -xcconfig ${OVERLAY} (shared CAS + prefix mapping), no project edit`);
}

// ---- CocoaPods / RN iOS
const podfile = read(join(IOS, 'Podfile'));
if (isRN && podfile) {
  const lock = read(join(IOS, 'Podfile.lock')), manifest = read(join(IOS, 'Pods/Manifest.lock'));
  if (existsSync(join(IOS, 'Pods')) && lock !== manifest)
    add('med', 'ios-pods-stale', 'ios/Pods/Manifest.lock', 'Pods out of sync with Podfile.lock: the build fails until pod install',
      `node ${RNFP} build (clones Pods from a sibling worktree, else pod install)`);
  const prebuiltOff = /RCT_USE_PREBUILT_RNCORE['"]?\]?\s*=\s*['"]?0|RCT_USE_PREBUILT_RNCORE=0/.test(podfile + read(join(IOS, '.xcode.env')) + read(join(IOS, '.xcode.env.local')))
    || process.env.RCT_USE_PREBUILT_RNCORE === '0';
  if (rnMinor >= 81 && (prebuiltOff || (existsSync(join(IOS, 'Pods')) && !existsSync(join(IOS, 'Pods/React-Core-prebuilt')))))
    add('high', 'ios-prebuilt-core-off', 'ios/Podfile', `RN ${rnVer} compiles React Native core from source`,
      'RCT_USE_PREBUILT_RNCORE=1 RCT_USE_RN_DEP=1 pod install (default since 0.84; debug into RN with RCT_SYMBOLICATE_PREBUILT_FRAMEWORKS=1 instead)');
  if (/flipper/i.test(podfile))
    add('med', 'ios-flipper', `ios/Podfile:${lineOf(podfile, /flipper/i)}`, 'Flipper pods add build time (removed upstream in 0.74)', 'remove Flipper; use React Native DevTools');
  if (/^\s*use_frameworks!/m.test(podfile) && !/ENV\[['"]USE_FRAMEWORKS/.test(podfile))
    add('low', 'ios-use-frameworks', `ios/Podfile:${lineOf(podfile, /^\s*use_frameworks!/m)}`, 'dynamic frameworks for every pod: more link and embed work', 'only if a dependency needs it; prefer USE_FRAMEWORKS=static via env');
  if (/hermes_enabled\s*=>\s*false/.test(podfile))
    add('low', 'ios-hermes-off', 'ios/Podfile', 'JSC instead of Hermes', 'enable Hermes');
  if (/^[^#\n]*ccache_enabled\s*=>\s*true/m.test(podfile) || process.env.USE_CCACHE)
    add('low', 'ios-ccache-and-cas', 'ios/Podfile', 'ccache replaces CC; unmeasured together with the Xcode compilation cache', 'use one: prefer the compilation cache (fast-sim.xcconfig)');
}

// ---- Metro / JS
if (isRN) {
  const scripts = Object.entries(pkg.scripts || {}).filter(([, v]) => /--reset-cache|resetCache/.test(v));
  for (const [k] of scripts) add('med', 'metro-reset-cache', `package.json scripts.${k}`, 'throws away the shared Metro transform cache on every start', 'drop --reset-cache; bump cacheVersion in metro.config.js when babel config changes');
  const metro = ['metro.config.js', 'metro.config.cjs', 'metro.config.ts'].map(f => [f, read(join(ROOT, f))]).find(([, t]) => t);
  if (metro && /resetCache\s*:\s*true/.test(metro[1])) add('med', 'metro-reset-cache', `${metro[0]}:${lineOf(metro[1], /resetCache/)}`, 'resetCache: true re-transforms everything each start', 'remove it');
  if (metro && !/maxWorkers/.test(metro[1]) && git('worktree', 'list').trim().split('\n').length > 1)
    add('low', 'metro-max-workers', metro[0], 'each Metro takes ~75% of cores; several worktrees oversubscribe', 'start with --max-workers 4 (local script, not committed)');
  try { execFileSync('watchman', ['--version'], { stdio: 'ignore' }); } catch { add('med', 'watchman-missing', 'PATH', 'Metro falls back to the slow node crawler', 'brew install watchman'); }
}

// ---- Expo CNG: ios/ and android/ come from `expo prebuild`; build config lives in app.json / app.config.*
const appCfg = ['app.config.js', 'app.config.ts', 'app.config.mjs', 'app.json'].map(f => [f, read(join(ROOT, f))]).find(([, t]) => t);
if (isRN && pkg.dependencies?.expo && appCfg) {
  const [cf, ct] = appCfg;
  if (!existsSync(IOS)) add('low', 'expo-cng', cf, 'ios/ and android/ are generated by expo prebuild, so Xcode/Pods checks have nothing to read yet',
    'npx expo prebuild -p ios (without --clean), then rerun this audit');
  if (/buildReactNativeFromSource\s*:\s*true/.test(ct)) {
    const patched = list('.patch', 3).filter(p => /react-native[@+]/.test(basename(p)))
      .flatMap(p => [...read(join(ROOT, p)).matchAll(/^diff --git a\/(\S+\.(?:mm|m|cpp|h|kt|java))/gm)].map(m => m[1]));
    add('high', 'expo-rn-from-source', `${cf}:${lineOf(ct, /buildReactNativeFromSource\s*:\s*true/)}`,
      patched.length ? `React Native core compiled from source, required: patches/ change ${patched.length} RN native files (prebuilt core can't carry them)`
        : 'React Native core compiled from source on every clean build',
      patched.length ? 'keep it; make the compile cheap: compilation cache overlay (or ccache with base_dir across worktrees) + a build cache. Local hack for work far from the patched files: RCT_USE_PREBUILT_RNCORE=1 at pod install; it drops the patches, so never verify UI with it'
        : 'set buildReactNativeFromSource: false, or locally RCT_USE_PREBUILT_RNCORE=1 at pod install');
  }
  if (/ccacheEnabled\s*:/.test(ct)) add('low', 'ios-ccache-and-cas', `${cf}:${lineOf(ct, /ccacheEnabled/)}`, 'ccache replaces CC; unmeasured together with the Xcode compilation cache; without base_dir each worktree path misses', 'pick one; for ccache across worktrees set base_dir to their common parent and hash_dir=false (~/.config/ccache/ccache.conf, local)');
  const bcp = ct.match(/buildCacheProvider\s*:\s*['"]?(\w+)/);
  if (!bcp) add('high', 'expo-no-build-cache', cf, '`expo run:ios` rebuilds native even for a fingerprint that was built before',
    'local: a buildCacheProvider plugin backed by ~/.cache, or rnfp after prebuild');
  else if (bcp[1] === 'eas') add('low', 'expo-build-cache-eas', `${cf}:${lineOf(ct, /buildCacheProvider/)}`,
    'build cache is EAS-hosted: hits need `eas login` with project access and an uploaded build for that fingerprint',
    'without access: a local provider ({plugin: "./local-cache.plugin.js"}, listed in .git/info/exclude) on the same fingerprint, or rnfp after prebuild');
  for (const [k, v] of Object.entries(pkg.scripts || {})) {
    if (/prebuild[^&|;]*--clean/.test(v)) add('med', 'expo-prebuild-clean', `package.json scripts.${k}`, '--clean deletes ios/ and android/ (Pods, codegen, DerivedData links, local hacks); the next build reinstalls pods',
      'run `expo prebuild` without --clean unless a config plugin changed (fingerprint diff says so)');
    if (/expo start\b[^&|;]*(\s-c\b|--clear)/.test(v)) add('med', 'metro-reset-cache', `package.json scripts.${k}`, 'expo start -c/--clear throws away the Metro transform cache',
      'drop -c; clear only after babel/metro config changes');
  }
}

// ---- Android
const gp = read(join(ROOT, 'android/gradle.properties')), ugp = read(join(homedir(), '.gradle/gradle.properties'));
if (gp) {
  const has = k => new RegExp(`^\\s*${k.replace(/\./g, '\\.')}\\s*=\\s*true`, 'm').test(gp + '\n' + ugp);
  for (const [k, why] of [['org.gradle.configuration-cache', 'reconfigures the whole build every run'], ['org.gradle.caching', 'no task output reuse across worktrees/branches'], ['org.gradle.parallel', 'modules build one at a time']])
    if (!has(k)) add('med', `android-${k.split('.').pop()}`, 'android/gradle.properties', why, `local: echo "${k}=true" >> ~/.gradle/gradle.properties (user-level wins over the project)`);
  const abis = (gp.match(/^\s*reactNativeArchitectures\s*=\s*(.+)$/m)?.[1] || '').split(',').filter(Boolean);
  if (abis.length > 1 && !/reactNativeArchitectures/.test(ugp))
    add('high', 'android-all-abis', `android/gradle.properties:${lineOf(gp, /reactNativeArchitectures/)}`, `native code built for ${abis.length} ABIs`,
      'local: echo "reactNativeArchitectures=arm64-v8a" >> ~/.gradle/gradle.properties, or run-android --active-arch-only');
}

// ---- Worktrees and DerivedData
const wts = git('worktree', 'list').trim().split('\n').filter(Boolean);
if (isRN && podfile && wts.length > 1)
  add('high', 'worktrees-cold-native', `${wts.length} git worktrees`, 'each worktree compiles the same native app from scratch',
    `node ${RNFP} build --udid <sim> in each worktree: same native inputs = reuse, JS-only Release = rebundle`);
const dd = join(homedir(), 'Library/Developer/Xcode/DerivedData');
const names = new Set(projects.map(p => basename(dirname(p), '.xcodeproj')));
if (existsSync(dd)) {
  const mine = readdirSync(dd).filter(d => names.has(d.replace(/-[a-z]{28}$/, '')));
  const stale = mine.filter(d => { try { return !existsSync(execFileSync('plutil', ['-extract', 'WorkspacePath', 'raw', join(dd, d, 'info.plist')], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()); } catch { return false; } });
  if (stale.length) add('low', 'derived-data-stale', `${dd} (${stale.length} of ${mine.length})`, 'DerivedData of checkouts that no longer exist',
    `rm -rf ${stale.slice(0, 3).map(d => `"${join(dd, d)}"`).join(' ')}${stale.length > 3 ? ' ...' : ''}`);
}

// ---- known patches from other apps (patch-scout library) that apply to this repo's installed packages
const SCOUT = join(SKILLS, 'patch-scout', 'scripts', 'scout.mjs');
if (isRN && existsSync(SCOUT)) {
  try {
    const rows = JSON.parse(execFileSync('node', [SCOUT, 'scan', ROOT, '--json'], { encoding: 'utf8' })).rows.filter(r => r.state === 'applies');
    const perf = rows.filter(r => r.category === 'perf');
    if (rows.length) add(perf.length ? 'med' : 'low', 'known-patches', `${rows.length} apply (${perf.length} perf: ${perf.map(r => r.id).join(', ') || 'none'})`,
      'fixes other RN apps ship for these exact packages; perf ones come with how they were measured',
      `node ${SCOUT} scan ${ROOT}, then A/B one with perf-climb (patch-scout skill)`);
  } catch {}
}

// ---- not an RN root: point at the RN apps inside (monorepos, app/ subdirs)
if (!isRN) for (const p of list('package.json', 3).filter(p => p && !/node_modules|fixtures?\//.test(p)))
  if (/"react-native"\s*:/.test(read(join(ROOT, p)))) add('low', 'rn-app-inside', dirname(p) || '.', 'React Native app below this root: RN, Metro and Android checks only run there', `node audit.mjs ${join(ROOT, dirname(p))}`);

// ---- report
const rank = { high: 0, med: 1, low: 2 };
findings.sort((a, b) => rank[a.win] - rank[b.win] || a.id.localeCompare(b.id));
if (JSON_OUT) { console.log(JSON.stringify({ root: ROOT, rn: rnVer || null, findings }, null, 1)); process.exit(0); }
console.log(`rn-build-audit  ${ROOT}  RN ${rnVer || '(none: native iOS checks only)'}  ${findings.length} findings\n`);
for (const f of findings) console.log(`[${f.win}] ${f.id}  ${f.where}\n   why: ${f.why}\n   fix: ${f.fix}\n`);
