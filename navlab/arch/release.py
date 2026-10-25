#!/usr/bin/env python3
"""release: what the JS/native split costs at release time, from git history (read-only).

  release.py <repo> [--since "2 years ago"] [--json out.json] [--metric store_share|both_per_month|ota_share]

- Each commit is JS-only (OTA-able if OTA is set up), native-only, or both (crosses the seam: a bridge change).
  Native: ios/, android/, *.swift|m|mm|h|kt|java|gradle, Podfile*, *.podspec, patches/ that patch a package's ios/ or
  android/, app.json native fields, app.config.* (dynamic, assumed native), and package.json `dependencies` changes that
  add/remove/upgrade a package with native code (node_modules/<pkg>/ios|android|*.podspec|expo-module.config.json).
  Without node_modules such packages are "unknown native?" and widen the store-release share to a range.
  Tests, e2e, docs, CI, lockfiles and files outside the app are ignored.
- The OTA setup (expo-updates / CodePush / ota-hot-update) and its runtimeVersion policy: what decides version skew.
- Bundle and binary size when a build is on disk.
`--metric` prints one number as the last line (for ledger.py measure).
"""
import json, os, re, subprocess, sys, time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cochange import history  # noqa: E402

NATIVE_EXT = re.compile(r'\.(swift|m|mm|h|hpp|c|cpp|kt|java|gradle|kts|pbxproj|plist|xcconfig|entitlements|storyboard|xib|podspec)$')
JS_EXT = re.compile(r'\.(js|jsx|ts|tsx|mjs|cjs|json|png|jpe?g|gif|webp|svg|ttf|otf|lottie|mp3|wav|mp4|graphql|gql)$')
IGNORE = re.compile(r'(^|/)(__tests__|__mocks__|e2e|cypress|maestro|docs?|fastlane|\.github|\.husky|scripts)/|\.(test|spec|stories)\.|'
                    r'(^|/)(yarn\.lock|package-lock\.json|pnpm-lock\.yaml|README[^/]*|CHANGELOG[^/]*)$|^\.')
VERSIONISH = re.compile(r'(CFBundleShortVersionString|CFBundleVersion|^\s*<string>[\d.]+</string>\s*$|versionCode|versionName|'
                        r'MARKETING_VERSION|CURRENT_PROJECT_VERSION|VERSION_CODE|VERSION_NAME|^COCOAPODS:|^PODFILE CHECKSUM:)')
OTA_APP_KEYS = {'extra', 'description', 'packagerOpts', 'githubUrl', 'assetBundlePatterns'}  # app.json keys an update can change
OTA_LIBS = {'expo-updates': 'expo-updates (EAS Update)', 'react-native-code-push': 'CodePush',
            '@bravemobile/react-native-code-push': 'CodePush (community fork)', '@revopush/react-native-code-push': 'CodePush (Revopush)',
            'react-native-ota-hot-update': 'react-native-ota-hot-update', 'react-native-update': 'Pushy (react-native-update)'}
SKEW = {
    'appVersion': 'runtime = app version: an update reaches every build with that version string. A native change shipped '
                  'without a version bump lets new JS land on an old binary (missing native module -> crash). Bump on every native change.',
    'nativeVersion': 'runtime = version + build number: every store build is its own runtime, so skew is safe but each update '
                     'must be published per build still in the wild.',
    'fingerprint': 'runtime = hash of the native layer: updates only reach binaries whose native code matches. Safest; any '
                   'native dep change silently starts a new runtime, so users on old binaries stop getting JS fixes until they update.',
    'sdkVersion': 'legacy policy (Expo SDK version): native changes within one SDK are invisible to it, high skew risk.',
    'fixed': 'fixed string: manual. Forgetting to bump it on a native change ships JS to binaries that lack the native '
             'code. Highest skew risk; needs a CI check that native files changed => runtimeVersion changed.',
    'codepush': 'CodePush targets the binary version (Info.plist / build.gradle versionName) given at release time: same risk '
                'as appVersion. Native change + same version = JS hitting a missing native module.',
    'none': 'no OTA: every change, JS or native, ships through the store. No version skew between JS and native (they ship '
            'together), only between app versions and the backend. JS-only share below = what OTA could ship without review.',
}


def git(d, *args):
    return subprocess.run(['git', '-C', d, *args], capture_output=True, text=True)


class Repo:
    """git show for repo-relative paths, in whichever repo holds the commits history() returned (app below a git root,
    or a nested .git with the real history in the parent)."""

    def __init__(self, repo, probe_hash):
        self.repo = os.path.realpath(repo)  # git reports real paths (gap g0006)
        tops = [git(repo, 'rev-parse', '--show-toplevel').stdout.strip()]
        tops.append(git(os.path.dirname(tops[0]), 'rev-parse', '--show-toplevel').stdout.strip())
        self.top = next((t for t in tops if t and git(t, 'cat-file', '-e', probe_hash + '^{commit}').returncode == 0), tops[0])

    def show(self, rev, rel):
        r = git(self.top, 'show', f'{rev}:{os.path.relpath(os.path.join(self.repo, rel), self.top)}')
        return r.stdout if r.returncode == 0 else None


def native_pkg(repo, pkg):
    """True/False when node_modules/<pkg> is installed, None (unknown) otherwise."""
    d = os.path.join(repo, 'node_modules', pkg)
    if not os.path.isdir(d):
        return None
    names = os.listdir(d)
    return any(n in ('ios', 'android', 'expo-module.config.json') or n.endswith('.podspec') for n in names)


def load_json(s):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}


def version_only(g, h, f):
    """True when every changed line is a version/build number (or a CocoaPods version/checksum line): a release
    bump, not native code."""
    if not re.search(r'(Info\.plist|build\.gradle|project\.pbxproj|gradle\.properties|Podfile\.lock)$', f):
        return False
    d = git(g.top, 'show', '--format=', '-U0', h, '--', os.path.relpath(os.path.join(g.repo, f), g.top)).stdout
    lines = [l[1:] for l in d.split('\n') if l[:1] in ('+', '-') and not l.startswith(('+++', '---'))]
    return bool(lines) and all(VERSIONISH.search(l) for l in lines)


def classify_file(f, h, g, repo, cache, rnconfig=False):
    """-> (kind, detail): kind in js | native | unknown | release | other."""
    if rnconfig and re.match(r'\.env(\.|$)', f):
        return 'native', ['react-native-config bakes .env into the build']
    if f.startswith('../') or IGNORE.search(f):
        return 'other', None
    base = os.path.basename(f)
    if f == 'package.json':
        old, new = load_json(g.show(h + '^', f)).get('dependencies', {}), load_json(g.show(h, f)).get('dependencies', {})
        changed = sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))
        if not changed:
            return 'other', None
        kinds = {p: cache.setdefault(p, native_pkg(repo, p)) for p in changed}
        nat = [p for p, k in kinds.items() if k]
        unk = [p for p, k in kinds.items() if k is None]
        likely = lambda p: p + ('*' if re.search(r'react-native|^@react-native|expo|^rn-', p) else '')
        return ('native', nat) if nat else ('unknown', [likely(p) for p in unk]) if unk else ('js', changed)
    if f == 'app.json':
        old, new = load_json(g.show(h + '^', f)), load_json(g.show(h, f))
        old, new = old.get('expo', old), new.get('expo', new)
        keys = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        return ('native', keys) if set(keys) - OTA_APP_KEYS else ('js', keys)
    if re.match(r'app\.config\.(js|ts|mjs|cjs)$', f):
        return 'native', ['app.config (dynamic, assumed native)']
    if f.startswith('patches/') and f.endswith('.patch'):
        body = g.show(h, f) or g.show(h + '^', f) or ''  # deleted patch: read the old one
        return ('native', [base]) if re.search(r'^diff --git a/node_modules/\S+?/(ios|android)/', body, re.M) else ('js', [base])
    if f.split('/')[0] in ('ios', 'android') or NATIVE_EXT.search(f) or base.startswith('Podfile'):
        return ('release', None) if version_only(g, h, f) else ('native', None)
    if JS_EXT.search(f):
        return 'js', None
    return 'other', None


def ota_setup(repo):
    pkg = load_json(open(os.path.join(repo, 'package.json')).read()) if os.path.exists(os.path.join(repo, 'package.json')) else {}
    deps = {**pkg.get('devDependencies', {}), **pkg.get('dependencies', {})}
    libs = [f'{OTA_LIBS[d]} {deps[d]}' for d in OTA_LIBS if d in deps]
    evidence, policy = [], None
    cands = ['app.json', 'app.config.js', 'app.config.ts', 'eas.json', 'android/app/src/main/res/values/strings.xml',
             'android/app/src/main/AndroidManifest.xml']
    ios = os.path.join(repo, 'ios')
    if os.path.isdir(ios):
        for d in os.listdir(ios):
            for n in ('Expo.plist', 'Supporting/Expo.plist', 'Info.plist'):
                if os.path.isfile(os.path.join(ios, d, n)):
                    cands.append(f'ios/{d}/{n}')
    pat = re.compile(r'runtimeVersion|EXUpdatesRuntimeVersion|expo_runtime_version|EXPO_RUNTIME_VERSION|CodePushDeploymentKey|"channel"')
    for rel in cands:
        p = os.path.join(repo, rel)
        if not os.path.isfile(p):
            continue
        lines = open(p, errors='ignore').read().split('\n')
        for i, line in enumerate(lines):
            if not pat.search(line):
                continue
            ctx = ' '.join(lines[i:i + 3])
            evidence.append(f'{rel}:{i + 1}: {line.strip()[:100]}')
            m = re.search(r'policy["\']?\s*:\s*["\'](\w+)', ctx)
            if m:
                policy = policy or m.group(1)
            elif 'runtimeVersion' in line or 'RuntimeVersion' in line or 'runtime_version' in line or 'RUNTIME_VERSION' in line:
                v = re.search(r'runtimeVersion["\']?\s*:\s*["\']([^"\']+)|<string>([^<]+)</string>|>([^<]+)<|value="([^"]+)"', ctx)
                if v and not policy:
                    val = next(x for x in v.groups() if x)
                    policy = val if val in SKEW else 'fixed'
    if not libs:
        key = 'none'
    elif any('expo-updates' in l for l in libs):
        key = policy or 'unset'
    elif any('CodePush' in l for l in libs):
        key = 'codepush'
    else:
        key = 'fixed'
    return {'libs': libs, 'policy': key, 'evidence': evidence,
            'skew': SKEW.get(key, 'expo-updates without a runtimeVersion: EAS Update requires one (SDK 50+); check how '
                                  'builds and updates are matched before trusting OTA.')}


def sizes(repo):
    roots = ['ios/build', 'android/app/build', 'dist', 'ios/main.jsbundle', 'android/app/src/main/assets']
    out = []

    def du(p):
        return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs
                   if not os.path.islink(os.path.join(dp, f)))

    def kind(p):
        with open(p, 'rb') as fh:
            return 'hermes bytecode' if fh.read(8) == b'\xc6\x1f\xbc\x03\xc1\x03\x19\x1f' else 'js'
    for r in roots:
        p = os.path.join(repo, r)
        if os.path.isfile(p):
            out.append({'file': r, 'bytes': os.path.getsize(p), 'kind': kind(p)})
        if not os.path.isdir(p):
            continue
        if r == 'dist':
            out.append({'file': 'dist/ (total)', 'bytes': du(p), 'kind': 'export'})
        for dp, dns, fs in os.walk(p):
            for d in [d for d in dns if d.endswith('.app')]:
                out.append({'file': os.path.relpath(os.path.join(dp, d), repo), 'bytes': du(os.path.join(dp, d)), 'kind': 'ios app'})
            dns[:] = [d for d in dns if not d.endswith('.app') and d != 'node_modules']
            for f in fs:
                fp = os.path.join(dp, f)
                if f in ('main.jsbundle', 'index.android.bundle') or re.search(r'\.(hbc|apk|aab|ipa)$', f) or \
                        (r == 'dist' and '/_expo/static/js/' in fp and f.endswith('.js')):
                    out.append({'file': os.path.relpath(fp, repo), 'bytes': os.path.getsize(fp),
                                'kind': f.rsplit('.', 1)[-1] if re.search(r'\.(apk|aab|ipa)$', f) else kind(fp)})
    out += [{'file': f, 'bytes': os.path.getsize(os.path.join(repo, f)), 'kind': f.rsplit('.', 1)[-1]}
            for f in os.listdir(repo) if re.search(r'\.(apk|aab|ipa)$', f)]  # archives left at the root
    return [s for s in out if s['bytes'] >= 10_000]  # placeholders (a readme in dist/) are not builds


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    repo, since = os.path.abspath(a[0]), opt('--since', '2 years ago')
    commits = history(repo, since, renames=False)
    nogit = commits is None
    commits = commits or []
    rows, cache, unknown_pkgs, native_pkgs, seam_files = [], {}, Counter(), Counter(), Counter()
    g = Repo(repo, commits[0]['hash']) if commits else None
    pj = os.path.join(repo, 'package.json')
    rnconfig = os.path.exists(pj) and 'react-native-config' in load_json(open(pj).read()).get('dependencies', {})
    for c in commits:
        per = {f: classify_file(f, c['hash'], g, repo, cache, rnconfig) for f in c['files']}
        kinds, details = Counter(k for k, _ in per.values()), []
        for f, (k, det) in per.items():
            if k == 'native' and f == 'package.json':
                native_pkgs.update(det)
            if k == 'unknown':
                unknown_pkgs.update(det)
            if k in ('native', 'unknown') and det:
                details.append(f'{f}: {"unknown native? " if k == "unknown" else ""}{", ".join(det)}')
        js, nat, unk = kinds['js'] > 0, kinds['native'] > 0, kinds['unknown'] > 0
        cls = 'both' if js and nat else 'native' if nat else 'js?' if unk else 'js' if js else \
            'release' if kinds['release'] else 'other'
        nat_files = [f for f, (k, _) in per.items() if k == 'native']
        if cls == 'both':
            seam_files.update(nat_files)
        rows.append({'hash': c['hash'][:10], 'date': time.strftime('%Y-%m-%d', time.localtime(c['time'])), 'msg': c['msg'][:80],
                     'class': cls, 'details': details, 'native_files': nat_files[:6],
                     'js_files': [f for f, (k, _) in per.items() if k == 'js'][:6]})
    app = [r for r in rows if r['class'] not in ('other', 'release')]
    n = Counter(r['class'] for r in app)
    total = len(app)
    ota = ota_setup(repo)
    sz = sizes(repo)
    res = {'repo': repo, 'since': since, 'commits': len(rows), 'app_commits': total, 'counts': dict(n), 'ota': ota, 'sizes': sz}
    if total:
        t = sorted(time.mktime(time.strptime(r['date'], '%Y-%m-%d')) for r in app)
        months = max(1.0, (t[-1] - t[0]) / 86400 / 30.44)
        store = (n['native'] + n['both']) / total
        res.update(window=f"{app[-1]['date']}..{app[0]['date']}", months=round(months, 1), store_share=round(store, 3),
                   store_share_hi=round(store + n['js?'] / total, 3), ota_share=round(n['js'] / total, 3),
                   both_share=round(n['both'] / total, 3), both_per_month=round(n['both'] / months, 2),
                   store_per_month=round((n['native'] + n['both']) / months, 2),
                   unknown_native=unknown_pkgs.most_common(), native_deps_changed=native_pkgs.most_common(),
                   seam_files=seam_files.most_common(15), commits_both=[r for r in rows if r['class'] == 'both'],
                   commits_unknown=[r for r in rows if r['class'] == 'js?'])
    if '--json' in a:
        json.dump(dict(res, rows=rows), open(opt('--json'), 'w'), indent=1)
    if '--metric' in a:
        print(res.get(opt('--metric'), 'n/a'))
        return
    print(f'release: {repo}')
    if nogit:
        print('  not a git repository: no commit split (OTA setup and build sizes below still apply)')
    elif not total:
        mine = [c for c in history(repo, '50 years ago', renames=False) or [] if any(not f.startswith('../') for f in c['files'])]
        last = time.strftime('%Y-%m-%d', time.localtime(mine[0]['time'])) if mine else 'none'
        print(f'  no app commits since {since} ({len(rows)} commits in window). Latest commit touching the app: {last}; '
              f'rerun with --since "<N> years ago".')
    else:
        n_rel = sum(r['class'] == 'release' for r in rows)
        rng = f'{store:.0%}' if not n['js?'] else f'{store:.0%}-{res["store_share_hi"]:.0%}'
        print(f'  {total} app commits {res["window"]} ({res["months"]} months; {n_rel} version-bump-only; {len(rows) - total - n_rel} touched no shipped file)')
        print(f'  JS-only {n["js"]} ({n["js"] / total:.0%})  native-only {n["native"]}  both {n["both"]} ({n["both"] / total:.0%})'
              f'  JS + unknown native dep {n["js?"]}')
        print(f'  needs a store release: {rng} of commits, {res["store_per_month"]}/month; OTA-able: {res["ota_share"]:.0%}')
        print(f'  boundary tax: {res["both_per_month"]} cross-seam commits/month (each = native-first release + version gate under OTA)')
        if res['seam_files']:
            print('  native files changed together with JS most often (where the bridge surface is):')
            for f, k in res['seam_files'][:8]:
                print(f'    {k:3}  {f}')
        if native_pkgs:
            print('  native dependencies added/removed/upgraded: ' + ', '.join(f'{p} x{k}' for p, k in native_pkgs.most_common(10)))
        if unknown_pkgs:
            print('  unknown native? (not in node_modules; install deps to resolve; * = name suggests native): ' +
                  ', '.join(f'{p} x{k}' for p, k in unknown_pkgs.most_common(12)))
        for r in res['commits_both'][:6]:
            print(f"    both  {r['hash']} {r['date']} {r['msg'][:50]}  native: {', '.join(r['native_files'][:2])}  js: {', '.join(r['js_files'][:2])}")
    print(f'\nOTA: {", ".join(ota["libs"]) or "none"}; runtimeVersion policy: {ota["policy"]}')
    for e in ota['evidence'][:6]:
        print(f'  {e}')
    print(f'  skew: {ota["skew"]}')
    print('\nbuild artifacts:' if sz else '\nbuild artifacts: no build found (ios/build, android/app/build, dist/, ios/main.jsbundle)')
    for s in sorted(sz, key=lambda s: -s['bytes'])[:10]:
        print(f"  {s['bytes'] / 1e6:8.2f} MB  {s['kind']:<15} {s['file']}")


if __name__ == '__main__':
    main()
