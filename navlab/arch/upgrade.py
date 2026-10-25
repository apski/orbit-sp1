#!/usr/bin/env python3
"""upgrade: is the platform under a migration or redesign stable? (read-only on the repo)

  upgrade.py <repo> [--json out.json] [--offline] [--cache ~/.cache/navlab/npm-view.json] [--metric risky|legacy|deprecated]

- Every package.json dependency (dev deps weighted half): installed version (node_modules, walking up for hoisting),
  latest + deprecated + last publish from the public registry (`npm view`, read-only, cached 7 days; --offline uses the
  cache only), major-version lag (minor lag for 0.x).
- New Architecture readiness per installed native package: ready (codegenConfig, TurboModule/Fabric specs, Expo module),
  interop-only (legacy RCT_EXPORT_* / @ReactMethod / ViewManager and no spec), unknown. Plus the app's newArchEnabled.
- React Native vs latest stable; Expo SDK vs latest and vs the versions that SDK expects (expo/bundledNativeModules.json).
- Ranked risks: deprecated, non-registry (git) deps, legacy-only native modules, unmaintained (latest publish > 2 years),
  big major lags. `--metric` prints one number as the last line (for ledger.py measure).
"""
import json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from release import load_json  # noqa: E402

TTL = 7 * 86400
# specs, or explicit new-arch code paths (C++/JSI libraries like reanimated have no codegen spec)
SPEC = re.compile(r'TurboModuleRegistry\.(get|getEnforcing)\b|codegenNativeComponent|codegenNativeCommands|RCT_NEW_ARCH_ENABLED|IS_NEW_ARCHITECTURE_ENABLED')
LEGACY = re.compile(r'RCT_EXPORT_MODULE|RCT_EXPORT_METHOD|RCT_EXTERN_MODULE|RCT_EXPORT_VIEW_PROPERTY|@ReactMethod|'
                    r'extends\s+(ReactContextBaseJavaModule|SimpleViewManager|ViewGroupManager)|:\s*(ReactContextBaseJavaModule|SimpleViewManager)\(')
SRC_EXT = re.compile(r'\.(m|mm|h|swift|java|kt|ts|tsx|js)$')
SKIP_DIRS = {'node_modules', 'example', 'examples', 'Example', 'docs', '__tests__', '.git', 'fixtures', 'build'}


def ver(v):
    m = re.match(r'[~^>=v ]*(\d+)\.(\d+)\.(\d+)', v or '')
    return tuple(map(int, m.groups())) if m else None


def lag(inst, latest):
    """(n, unit): majors behind, or minors behind for 0.x."""
    a, b = ver(inst), ver(latest)
    if not a or not b:
        return 0, ''
    if a[0] == b[0] == 0:
        return b[1] - a[1], 'minor (0.x)'
    return b[0] - a[0], 'major'


def installed(repo, pkg):
    d = repo
    for _ in range(4):  # hoisted installs live in a parent node_modules
        p = os.path.join(d, 'node_modules', pkg, 'package.json')
        if os.path.exists(p):
            return load_json(open(p).read()), os.path.dirname(p)
        d = os.path.dirname(d)
    return None, None


def npm_view(pkg, cache, offline):
    hit = cache.get(pkg)
    if hit and (offline or (time.time() - hit['at'] < TTL and 'latest_codegen' in hit['data'])):
        return hit['data']
    if offline:
        return None
    r = subprocess.run(['npm', 'view', pkg, 'version', 'deprecated', 'time', 'codegenConfig', '--json', '--registry', 'https://registry.npmjs.org'],
                       capture_output=True, text=True, cwd=os.path.expanduser('~'), timeout=60)
    d = load_json(r.stdout)
    if r.returncode or 'error' in d:
        data = {'error': (d.get('error') or {}).get('code', 'npm view failed')}
    else:
        t = d.get('time', {})
        data = {'latest': d.get('version'), 'deprecated': d.get('deprecated'),
                'published': (t.get(d.get('version')) or t.get('modified') or '')[:10], 'latest_codegen': bool(d.get('codegenConfig'))}
    cache[pkg] = {'at': time.time(), 'data': data}
    return data


def arch_readiness(pkg_dir, meta):
    """-> (status, evidence)."""
    if meta.get('codegenConfig'):
        return 'ready', 'package.json codegenConfig'
    if os.path.exists(os.path.join(pkg_dir, 'expo-module.config.json')):
        return 'ready', 'expo-module.config.json (Expo Modules API)'
    legacy, seen = None, 0
    for dp, dns, fs in os.walk(pkg_dir):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fs:
            if not SRC_EXT.search(f) or seen > 4000:
                continue
            seen += 1
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) > 400_000:
                    continue
                text = open(p, errors='ignore').read()
            except OSError:
                continue
            m = SPEC.search(text)
            if m:
                return 'ready', f'{os.path.relpath(p, pkg_dir)}:{text[:m.start()].count(chr(10)) + 1} {m.group(0)}'
            m = not legacy and LEGACY.search(text)
            if m:
                legacy = f'{os.path.relpath(p, pkg_dir)}:{text[:m.start()].count(chr(10)) + 1} {m.group(0)}'
    if legacy:
        return 'interop-only', legacy
    unimodule = os.path.exists(os.path.join(pkg_dir, 'unimodule.json'))
    return 'unknown', 'unimodule.json (legacy Expo module)' if unimodule else 'no spec and no legacy macro found'


def app_new_arch(repo):
    out = []
    for rel in ('android/gradle.properties', 'ios/Podfile.properties.json', 'ios/Podfile', 'app.json', 'app.config.js', 'app.config.ts'):
        p = os.path.join(repo, rel)
        if not os.path.isfile(p):
            continue
        for i, line in enumerate(open(p, errors='ignore')):
            if re.search(r'newArchEnabled|RCT_NEW_ARCH_ENABLED|new_arch_enabled', line) and not line.lstrip().startswith('#'):
                out.append(f'{rel}:{i + 1}: {line.strip()[:90]}')
    return out


def sdk_mismatch(expected, inst_version):
    """Expo's bundledNativeModules range vs installed: ~ pins major.minor, ^ pins major (minor for 0.x)."""
    e, i = ver(expected), ver(inst_version)
    if not e or not i:
        return False
    if expected.startswith('^') and e[0] > 0:
        return e[0] != i[0]
    return e[:2] != i[:2]


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    repo, offline = os.path.abspath(a[0]), '--offline' in a
    cache_path = os.path.expanduser(opt('--cache', '~/.cache/navlab/npm-view.json'))
    cache = load_json(open(cache_path).read()) if os.path.exists(cache_path) else {}
    pj = load_json(open(os.path.join(repo, 'package.json')).read())
    deps = [(p, r, False) for p, r in pj.get('dependencies', {}).items()] + \
           [(p, r, True) for p, r in pj.get('devDependencies', {}).items() if p not in pj.get('dependencies', {})]
    has_nm = os.path.isdir(os.path.join(repo, 'node_modules'))
    registry = [p for p, r, _ in deps if not re.match(r'(git|github:|https?:|file:|link:|workspace:|npm:)|.*/.*#|^[\w-]+/[\w.-]+$', r)]
    with ThreadPoolExecutor(8) as ex:
        views = dict(zip(registry, ex.map(lambda p: npm_view(p, cache, offline), registry)))
    if not offline:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        json.dump(cache, open(cache_path, 'w'))
    rows = []
    for p, rng, dev in deps:
        meta, pdir = installed(repo, p)
        v = views.get(p) or {}
        inst = (meta or {}).get('version') or rng
        row = {'pkg': p, 'dev': dev, 'declared': rng, 'installed': (meta or {}).get('version'), 'latest': v.get('latest'),
               'deprecated': v.get('deprecated'), 'published': v.get('published'), 'registry': p in registry, 'error': v.get('error')}
        row['lag'], row['lag_unit'] = lag(inst, v.get('latest'))
        if pdir and not dev and p != 'react-native' and any(n in ('ios', 'android', 'expo-module.config.json') or n.endswith('.podspec')
                                                            for n in os.listdir(pdir)):
            row['native'] = True
            row['arch'], row['arch_evidence'] = arch_readiness(pdir, meta)
        # risk score
        score, why = 0, []
        if row['deprecated']:
            score += 100; why.append(f"deprecated: {str(row['deprecated'])[:80]}")
        local = str(rng).startswith(('link:./', 'file:./', 'link:../', 'file:../', 'workspace:'))
        if not row['registry'] and not local:
            score += 50; why.append(f'non-registry dependency ({rng[:50]})')
        elif local:
            row['local_module'] = True  # the app's own (native) module: part of its native surface, not a dependency risk
        if row.get('arch') == 'interop-only':
            fix = {True: 'latest has codegenConfig: upgrade fixes it', False: 'latest has no codegenConfig either: check its New Arch notes, else replace or wrap'}.get(v.get('latest_codegen'), '')
            score += 30 if v.get('latest_codegen') else 40
            why.append(f"legacy native module only ({row['arch_evidence']}{'; ' + fix if fix else ''})")
        elif row.get('arch') == 'unknown':
            score += 10; why.append('native, New Arch status unknown')
        if row['published'] and time.time() - time.mktime(time.strptime(row['published'], '%Y-%m-%d')) > 2 * 365 * 86400:
            yrs = (time.time() - time.mktime(time.strptime(row['published'], '%Y-%m-%d'))) / 365 / 86400
            score += (30 if row.get('native') else 15) + min(20, int(5 * (yrs - 2))); why.append(f"no publish since {row['published']} ({yrs:.1f}y)")
        if row['lag'] > 0:
            score += min(50, 10 * row['lag'] if row['lag_unit'] == 'major' else 3 * row['lag'])
            why.append(f"{row['lag']} {row['lag_unit']} behind ({inst} -> {row['latest']})")
        if row['error']:
            why.append(f"registry: {row['error']} (private package?)")
        row['score'], row['why'] = score // 2 if dev else score, why
        rows.append(row)
    rn = next((r for r in rows if r['pkg'] == 'react-native'), None)
    expo = next((r for r in rows if r['pkg'] == 'expo'), None)
    expected, mismatches = {}, []
    if expo and expo['installed']:
        expected = load_json(open(os.path.join(installed(repo, 'expo')[1], 'bundledNativeModules.json')).read()) \
            if os.path.exists(os.path.join(installed(repo, 'expo')[1], 'bundledNativeModules.json')) else {}
        for r in rows:
            if r['pkg'] in expected and r['installed'] and sdk_mismatch(expected[r['pkg']], r['installed']):
                mismatches.append((r['pkg'], r['installed'], expected[r['pkg']]))
    native = [r for r in rows if r.get('native')]
    risky = sorted([r for r in rows if r['score'] >= 30], key=lambda r: -r['score'])
    res = {'repo': repo, 'node_modules': has_nm, 'deps': len(rows), 'native': len(native),
           'ready': sum(r['arch'] == 'ready' for r in native), 'interop_only': sum(r['arch'] == 'interop-only' for r in native),
           'unknown_arch': sum(r['arch'] == 'unknown' for r in native), 'new_arch_setting': app_new_arch(repo),
           'deprecated': sum(bool(r['deprecated']) for r in rows), 'risky': len(risky), 'legacy': sum(r['arch'] == 'interop-only' for r in native),
           'expo_sdk_mismatch': mismatches, 'rows': rows}
    if '--json' in a:
        json.dump(res, open(opt('--json'), 'w'), indent=1)
    if '--metric' in a:
        print(res.get(opt('--metric'), 'n/a'))
        return
    print(f'upgrade: {repo}  ({len(rows)} deps, node_modules {"installed" if has_nm else "MISSING: versions are declared ranges, no New Arch scan"}'
          f'{", offline (cache only)" if offline else ""})')
    if rn:
        print(f"  react-native {rn['installed'] or rn['declared']} -> latest {rn['latest'] or '?'} "
              f"({rn['lag']} {rn['lag_unit']} behind)")
    if expo:
        print(f"  expo SDK {(ver(expo['installed'] or expo['declared']) or ['?'])[0]} (expo {expo['installed'] or expo['declared']}) -> latest "
              f"{(ver(expo['latest']) or ['?'])[0]}{'; SDK expects react-native ' + expected['react-native'] if 'react-native' in expected else ''}")
        for p, i, e in mismatches[:12]:
            print(f'    SDK mismatch  {p} {i}, SDK expects {e}')
    print(f"  newArchEnabled: {'; '.join(res['new_arch_setting']) or 'not set'}"
          f"{' (RN >= 0.76 defaults on; >= 0.82 is New Arch only)' if rn else ''}")
    if has_nm:
        print(f"  native packages: {len(native)}  ready {res['ready']}  interop-only {res['interop_only']}  unknown {res['unknown_arch']}")
        for r in [r for r in native if r['arch'] != 'ready']:
            nxt = {True: '  [latest: codegen]', False: '  [latest: no codegenConfig]'}.get(views.get(r['pkg'], {}).get('latest_codegen'), '')
            print(f"    {r['arch']:<13} {r['pkg']} {r['installed']}  {r['arch_evidence']}{nxt}")
    print(f"  deprecated {res['deprecated']}, risky (score >= 30) {len(risky)}")
    print('\nrisks, ranked:')
    for r in risky[:20]:
        print(f"  {r['score']:4}  {r['pkg']}{' (dev)' if r['dev'] else ''}: {'; '.join(r['why'])}")
    errs = [r['pkg'] for r in rows if r['error']]
    if errs:
        print(f"\nnot on the public registry: {', '.join(errs[:15])}")


if __name__ == '__main__':
    main()
