#!/usr/bin/env python3
"""profile: one repo profile for every NavLab skill. Detected once, confirmed by a human, read by all.

  profile.py detect [<repo>]           discover conventions -> navlab.config.json "profile" + navlab.adapter.json "facts"
  profile.py check <skill> [<repo>]    preflight for one skill: what it needs, what's confirmed/detected/missing, what to ask
  profile.py confirm <section> [<repo>]  mark a detected section as confirmed by the user (edit the value first if wrong)

Sections carry {value, status: detected|confirmed|missing, evidence}. detect never overwrites a confirmed section.
Skills start with `profile.py check <skill>`: exit 0 = usable (detected or confirmed), 1 = a required section is missing.
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ANALYTICS_PKGS = {'@react-native-firebase/analytics': ['logEvent', 'logScreenView'], 'posthog-react-native': ['capture', 'screen'],
                  '@segment/analytics-react-native': ['track', 'screen'], 'react-native-appsflyer': ['logEvent'],
                  '@amplitude/analytics-react-native': ['track'], 'mixpanel-react-native': ['track'], 'expo-firebase-analytics': ['logEvent']}
FLAG_PKGS = {'@launchdarkly/react-native-client-sdk': ['boolVariation', 'variation', 'useBoolVariation'], 'launchdarkly-react-native-client-sdk': ['boolVariation', 'variation'],
             'statsig-react-native': ['checkGate', 'useGate'], '@statsig/react-native-bindings': ['checkGate', 'useGateValue'],
             '@growthbook/growthbook-react': ['useFeatureIsOn', 'useFeatureValue'], 'configcat-react': ['useFeatureFlag'],
             '@react-native-firebase/remote-config': ['getValue', 'getBoolean'], 'posthog-react-native': ['useFeatureFlag', 'isFeatureEnabled']}
STORAGE_PKGS = ['@react-native-async-storage/async-storage', 'react-native-mmkv', 'expo-secure-store', 'react-native-keychain', 'react-native-encrypted-storage']
TELEMETRY_PKGS = {'@sentry/react-native': 'sentry', 'posthog-react-native': 'posthog', '@react-native-firebase/perf': 'firebase-perf',
                  '@datadog/mobile-react-native': 'datadog', '@bugsnag/react-native': 'bugsnag', 'newrelic-react-native-agent': 'newrelic'}
DS_PKG = re.compile(r'(^|[/@-])(ui|design|designsystem|design-system|ds|kit|components|primitives|tokens)([/-]|$)', re.I)
DS_DIR = re.compile(r'(^|/)(components|ui|design-system|designsystem|styleguide|theme|primitives)$', re.I)

NEEDS = {  # skill -> (required sections, optional sections)
    'navlab-audit': (['navigation'], ['usage']), 'navlab-install': (['navigation', 'ios'], ['analytics', 'flags', 'storage', 'apiClients', 'stateLibs']),
    'design-afresh': (['git'], ['analytics', 'flags', 'storage', 'apiClients']), 'contracts-scan': ([], ['analytics', 'flags', 'storage']),
    'ds-audit': (['designSystem'], ['nativeDesignSystem']), 'data-layer-audit': ([], ['apiClients', 'stateLibs']),
    'safety-net': (['e2e'], []), 'release-boundary': (['git'], ['ota']), 'field-perf': (['telemetry'], []),
    'upgrade-readiness': (['nodeModules'], []), 'rn-native-migration': (['git', 'navigation'], ['usage', 'ota', 'nativeDesignSystem', 'team']),
    'app-architecture-steward': (['git', 'navigation'], ['usage', 'telemetry']), 'android-parity': (['android'], []),
}
ASK = {
    'usage': 'Export screen views per route for the last 30-90 days (PostHog/Amplitude/Firebase) to .navlab/usage.json as {"Route": views}.',
    'team': 'Who owns Swift/Kotlin? (set profile.team.value, e.g. {"ios": 1, "android": 0, "rn": 4})',
    'designSystem': 'Which folders/packages are the design system? (facts.designSystem in navlab.adapter.json)',
    'nativeDesignSystem': 'Is there a native (SwiftUI/UIKit) design system folder? Its path, or "none".',
    'e2e': 'Where do e2e tests live (Maestro/Detox/argent flows)? None yet -> the safety-net skill writes them first.',
    'telemetry': 'Which production performance source can be exported (Sentry, PostHog, MetricKit)?',
    'git': 'This skill needs git history in the repo or a parent repo.',
    'navigation': 'Run node $NAVLAB_HOME/bootstrap.mjs . and resolve the navscan adapter todo (navlab-install §1).',
    'android': 'Android SDK + an emulator image or device (see android-parity §1).',
}


def load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True).stdout


FN = re.compile(r'export\s+(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)'
                r'|export\s+const\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:<[^>]*>\s*)?\(([^)]*)\)\s*(?::[^=]+)?=>'
                r'|export\s+const\s+(\w+)\s*=\s*(?:async\s+)?(\w+)\s*=>')


def exported_names(repo, files, pattern, first_param=None):
    """Exported functions (not enums/objects) whose name matches; optionally whose first parameter matches too."""
    out = set()
    for f in files:
        try:
            src = open(os.path.join(repo, f), errors='ignore').read()
        except OSError:
            continue
        for m in FN.finditer(src):
            name = m.group(1) or m.group(3) or m.group(5)
            params = (m.group(2) or m.group(4) or m.group(6) or '').split(',')[0]
            if re.search(pattern, name) and (first_param is None or re.search(first_param, params)):
                out.add(name)
    return sorted(out)


def detect(repo):
    pkg = load(os.path.join(repo, 'package.json'), {})
    deps = {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {})}
    facts_p = os.path.join(repo, '.navlab', 'facts.json')
    if not os.path.exists(facts_p):
        subprocess.run(['node', os.path.join(HERE, 'facts.mjs'), repo], capture_output=True)
    facts = load(facts_p, {'facts': []})['facts']
    importers = lambda pkgs: sorted({f['file'] for f in facts if not f.get('test') and any(i == p or i.startswith(p + '/') for i in f['imports'] for p in pkgs)})
    P = {}

    an_pkgs = [p for p in ANALYTICS_PKGS if p in deps]
    an_files = importers(an_pkgs)
    wrappers = exported_names(repo, an_files, r'(?i)track|log|event|capture|analytics')
    # fallback: methods called often with a literal event name, e.g. ax.metric('post:like', ...) via a custom hook
    calls = {}
    for f in facts:
        if f.get('test'):
            continue
        try:
            src = open(os.path.join(repo, f['file']), errors='ignore').read()
        except OSError:
            continue
        for m in re.finditer(r'\b\w+\.(metric|track|trackEvent|logEvent|capture|event|logMetric)\(\s*[\'"`]', src):
            calls[m.group(1)] = calls.get(m.group(1), 0) + 1
    frequent = [k for k, n in calls.items() if n >= 10]
    P['analytics'] = {'value': sorted({c for p in an_pkgs for c in ANALYTICS_PKGS[p]} | set(wrappers) | set(frequent)),
                      'evidence': [f'package {p}' for p in an_pkgs] + [f'wrapper in {f}' for f in an_files[:5]] + [f'.{k}("...") x{calls[k]}' for k in frequent]}
    fl_pkgs = [p for p in FLAG_PKGS if p in deps]
    hook_files = [f['file'] for f in facts if not f.get('test') and re.search(r'(?i)flag|feature|toggle|remoteconfig|experiment', f['file'])]
    P['flags'] = {'value': sorted({c for p in fl_pkgs for c in FLAG_PKGS[p]} | set(exported_names(repo, hook_files, r'^use\w*(Flag|Feature|Toggle|Experiment|Config)\w*$|^(is|get)\w*(Flag|Feature)\w*$'))),
                  'evidence': [f'package {p}' for p in fl_pkgs] + hook_files[:5]}
    st_files = importers(STORAGE_PKGS)
    # module-qualified (file#name): generic names like get/write only count when imported from the wrapper's module.
    # Wrappers are exported functions taking a key, or exported instances (export const device = new Storage(...)).
    qual = [f'{f}#{n}' for f in st_files for n in exported_names(repo, [f], r'.', first_param=r'(?i)key')]
    for f in st_files:
        src = open(os.path.join(repo, f), errors='ignore').read()
        qual += [f'{f}#{m.group(1)}' for m in re.finditer(r'export\s+const\s+(\w+)\s*(?::[^=]+)?=\s*new\s+\w+', src)]
    P['storage'] = {'value': sorted(set(qual)),
                    'evidence': [f'wraps storage: {f}' for f in st_files[:6]]}
    clients = set()
    for f in facts:
        if f.get('test') or not any(i.startswith('axios') or i in ('ky', 'apisauce') for i in f['imports']):
            continue
        src = open(os.path.join(repo, f['file']), errors='ignore').read()
        clients |= {m.group(1) for m in re.finditer(r'(?:const|let)\s+(\w+)\s*=\s*(?:axios|ky|apisauce)\.(?:create|extend)\(', src)}
    P['apiClients'] = {'value': sorted(clients) or [], 'evidence': ['axios.create / ky.create instances'] if clients else []}
    P['stateLibs'] = {'value': sorted(p for p in deps if p in ('redux', '@reduxjs/toolkit', 'zustand', 'mobx', 'mobx-state-tree', 'jotai', 'recoil', 'valtio', 'xstate',
                                                              '@tanstack/react-query', 'swr', '@apollo/client', 'react-relay', 'urql')), 'evidence': ['package.json']}
    # design system: packages that look like UI kits and are imported a lot; local folders that many files render from
    ds_pk = sorted(((sum(1 for f in facts if any(i == p or i.startswith(p + '/') for i in f['imports'])), p) for p in deps if DS_PKG.search(p)), reverse=True)
    src_dirs = {}
    for f in facts:
        for j in f['jsx'].values():
            s = j['src']
            if '/' in s and s.endswith(('.tsx', '.ts', '.js', '.jsx')):
                d = os.path.dirname(s)
                while d and not DS_DIR.search(d):
                    d = os.path.dirname(d)
                if d:
                    src_dirs[d] = src_dirs.get(d, 0) + j['count']
    top_dirs = sorted(src_dirs.items(), key=lambda x: -x[1])[:3]
    P['designSystem'] = {'value': [p for n, p in ds_pk if n >= 5][:3] + [d for d, _ in top_dirs],
                         'evidence': [f'{p}: imported by {n} files' for n, p in ds_pk[:3]] + [f'{d}: {n} renders' for d, n in top_dirs]}
    native_ds = []
    for root in ('ios', 'android'):
        for dp, dn, fn in os.walk(os.path.join(repo, root)):
            if 'Pods' in dp or 'build' in dp or 'node_modules' in dp:
                dn[:] = []
                continue
            if re.search(r'(?i)design.?system|components|ui$', os.path.basename(dp)) and any(f.endswith(('.swift', '.kt')) for f in fn):
                native_ds.append(os.path.relpath(dp, repo))
    P['nativeDesignSystem'] = {'value': native_ds[:3], 'evidence': native_ds[:3]}
    e2e = [d for d in ('maestro', '.maestro', 'e2e', '__e2e__', 'e2e-tests', 'tests/e2e', 'cypress', 'flows', '.argent/flows', 'detox') if os.path.isdir(os.path.join(repo, d))]
    if 'detox' in pkg or os.path.exists(os.path.join(repo, '.detoxrc.js')) or os.path.exists(os.path.join(repo, '.detoxrc.json')):
        e2e.append('detox config')
    P['e2e'] = {'value': e2e, 'evidence': e2e}
    ota = [p for p in ('expo-updates', 'react-native-code-push', 'react-native-ota-hot-update') if p in deps]
    app = load(os.path.join(repo, 'app.json'), {}).get('expo', {})
    P['ota'] = {'value': {'packages': ota, 'runtimeVersion': app.get('runtimeVersion')}, 'evidence': ota}
    tele = [v for p, v in TELEMETRY_PKGS.items() if p in deps]
    P['telemetry'] = {'value': tele, 'evidence': tele}
    from cochange import history
    commits = history(repo, '5 years ago') or []
    P['git'] = {'value': len(commits), 'evidence': [f'{len(commits)} commits (<=40 files) in 5 years']}
    P['navigation'] = {'value': os.path.exists(os.path.join(repo, 'navlab.adapter.json')), 'evidence': ['navlab.adapter.json'] if os.path.exists(os.path.join(repo, 'navlab.adapter.json')) else []}
    P['ios'] = {'value': os.path.isdir(os.path.join(repo, 'ios')) or bool(app), 'evidence': ['ios/'] if os.path.isdir(os.path.join(repo, 'ios')) else ['expo app.json']}
    P['usage'] = {'value': os.path.exists(os.path.join(repo, '.navlab', 'usage.json')), 'evidence': []}
    P['nodeModules'] = {'value': os.path.isdir(os.path.join(repo, 'node_modules')), 'evidence': []}
    P['android'] = {'value': bool(os.environ.get('ANDROID_HOME')) and os.path.isdir(os.path.join(repo, 'android')), 'evidence': [os.environ.get('ANDROID_HOME', '')]}
    P['team'] = {'value': None, 'evidence': []}

    cfg_p = os.path.join(repo, 'navlab.config.json')
    cfg = load(cfg_p, {})
    prof = cfg.get('profile', {})
    for k, v in P.items():
        if prof.get(k, {}).get('status') == 'confirmed':
            continue
        present = v['value'] not in (None, [], {}, False, 0) and v['value'] != {'packages': [], 'runtimeVersion': None}
        prof[k] = {**v, 'status': 'detected' if present else 'missing'}
    cfg['profile'] = prof
    json.dump(cfg, open(cfg_p, 'w'), indent=2)
    ad_p = os.path.join(repo, 'navlab.adapter.json')
    ad = load(ad_p, {})
    facts_cfg = ad.setdefault('facts', {})
    for k, key in (('analytics', 'analytics'), ('flags', 'flags'), ('storage', 'storage'), ('apiClients', 'apiClients'), ('designSystem', 'designSystem')):
        if prof[k]['status'] != 'missing' and (prof[k]['status'] == 'confirmed' or not facts_cfg.get(key)):
            facts_cfg[key] = prof[k]['value']
    json.dump(ad, open(ad_p, 'w'), indent=2)
    print(f'profile {repo}:')
    for k, v in prof.items():
        val = v['value'] if not isinstance(v['value'], list) else ', '.join(map(str, v['value'][:6])) + (' ...' if len(v['value']) > 6 else '')
        print(f'  {v["status"]:<9} {k:<18} {str(val)[:90]}')
    print('-> navlab.config.json profile, navlab.adapter.json facts. Re-run facts.mjs so the conventions apply; confirm with `profile.py confirm <section>`.')


def check(skill, repo):
    cfg = load(os.path.join(repo, 'navlab.config.json'), {})
    prof = cfg.get('profile')
    if not prof:
        print(f'no repo profile yet: run python3 {HERE}/profile.py detect {repo} (the install does this)')
        sys.exit(1)
    req, opt = NEEDS.get(skill, ([], []))
    bad = False
    print(f'{skill} preflight:')
    for k in req + opt:
        s = prof.get(k, {}).get('status', 'missing')
        need = 'required' if k in req else 'optional'
        print(f'  {s:<9} {k:<18} ({need}){"  ask: " + ASK[k] if s == "missing" and k in ASK else ""}')
        bad |= s == 'missing' and k in req
    detected = [k for k in req + opt if prof.get(k, {}).get('status') == 'detected']
    if detected:
        print(f'  confirm with the user once (then `profile.py confirm <section>`): {", ".join(detected)}')
    sys.exit(1 if bad else 0)


def confirm(section, repo):
    p = os.path.join(repo, 'navlab.config.json')
    cfg = load(p, {})
    cfg.setdefault('profile', {}).setdefault(section, {'value': None, 'evidence': []})['status'] = 'confirmed'
    json.dump(cfg, open(p, 'w'), indent=2)
    print(f'{section}: confirmed')


def main():
    a = sys.argv[1:]
    if not a or a[0] not in ('detect', 'check', 'confirm'):
        print(__doc__)
        return
    if a[0] == 'detect':
        detect(os.path.abspath(a[1] if len(a) > 1 else '.'))
    elif a[0] == 'check':
        check(a[1], os.path.abspath(a[2] if len(a) > 2 else '.'))
    else:
        confirm(a[1], os.path.abspath(a[2] if len(a) > 2 else '.'))


if __name__ == '__main__':
    main()
