#!/usr/bin/env python3
"""fitness: CI checks that keep an architecture from drifting back (the steward's "lock it in" step).

  fitness.py check <repo> [--config <repo>/navlab.fitness.json] [--navscan scan.json] [--edges edges.json] [--json out]
  fitness.py init <repo> [--design candidate.json ...] [--config out] [--navscan scan.json]   budgets = today's values, rules from designs
  fitness.py --selftest

Config (navlab.fitness.json):
  budget   {"max_lag_ms": 16.7, "max_tab_routes_above_nested": 5, "max_path_navigators": 4}      navscan budget:
           worst predicted RN-vs-native lag per push on any path; routes in a tab/drawer navigator that has nested
           navigators below it (AGENTS.md rules 1-2: the multiplier); navigators on one root->leaf path (rule 3)
  imports  [{"name", "from": [globs], "except": [globs], "never": [globs], "allow": [globs], "why"}]: a design's
           "must never import" (files matching from and not except must not import never minus allow)
  writers  [{"name", "calls": regex, "mentions": regex, "only": [globs], "why"}]: one owner per state; a file outside
           `only` that matches both regexes writes state it doesn't own
  rules    {"inline_component": true, "scrollview_map": true}: AGENTS.md rule 7 (navscan flag) and rule 6 (.map inside
           <ScrollView>, heuristic)
  accepted [violation ids]: what existed when the check was introduced. check fails only on NEW violations and lists
           accepted ones that are gone (run init again to tighten): a ratchet, so a legacy app can adopt it today.
Exit 1 on any new violation. Imports come from depgraph.mjs --edges (same resolver as navscan); globs are repo-relative.
"""
import json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from replay import matcher  # noqa: E402

DEFAULT_BUDGET = {'max_lag_ms': 16.7, 'max_tab_routes_above_nested': 5, 'max_path_navigators': 4}
SKIP = {'node_modules', '.git', 'ios', 'android', 'build', 'dist', 'coverage', 'vendor', '__tests__', '__mocks__', 'lexicons', '.expo', 'Pods'}
EXTS = ('.ts', '.tsx', '.js', '.jsx')


def source_files(repo):
    base = os.path.join(repo, 'src') if os.path.isdir(os.path.join(repo, 'src')) else repo
    for d, ds, fs in os.walk(base):
        ds[:] = [x for x in ds if x not in SKIP and not x.startswith('.')]
        for f in fs:
            if f.endswith(EXTS) and not f.endswith('.d.ts') and not re.search(r'\.(test|spec|stories)\.', f):
                yield os.path.relpath(os.path.join(d, f), repo)


def navscan(repo, path=None):
    if not path:
        path = os.path.join(tempfile.mkdtemp(), 'navscan.json')
        subprocess.run(['node', os.path.join(ROOT, 'scan', 'navscan.mjs'), repo, '--json', path], capture_output=True)
    return json.load(open(path)) if os.path.exists(path) else None


def edges_of(repo, path=None):
    if not path:
        path = os.path.join(tempfile.mkdtemp(), 'edges.json')
        subprocess.run(['node', os.path.join(HERE, 'depgraph.mjs'), repo, '--edges', path], capture_output=True)
    return json.load(open(path))


def measure(repo, cfg, scan_path=None, edges_path=None):
    """-> (values, violations): today's budget numbers and every violation id with its detail."""
    budget = {**DEFAULT_BUDGET, **cfg.get('budget', {})}
    rules = {'inline_component': True, 'scrollview_map': True, **cfg.get('rules', {})}
    vals, out = {}, {}
    scan = navscan(repo, scan_path)
    if scan:
        nodes = {n['id']: n for n in scan['tree'] if n.get('reached') is not False}
        paths = [p for p in scan['paths'] if p.get('lag') is not None and all(i in nodes for i in p['ids'])]
        vals['max_lag_ms'] = round(max((p['lag'] for p in paths), default=0), 1)
        vals['max_path_navigators'] = max((p['navigators'] for p in paths), default=0)
        big = [n for n in nodes.values() if n['kind'] == 'tabs' and n.get('children')]
        vals['max_tab_routes_above_nested'] = max((n['routes'] for n in big), default=0)
        for p in paths:
            if p['lag'] > budget['max_lag_ms']:
                out[f"lag:{p['at']}"] = f"{p['path']} predicted lag {p['lag']:.1f}ms > {budget['max_lag_ms']}ms"
            if p['navigators'] > budget['max_path_navigators']:
                out[f"depth:{p['at']}"] = f"{p['path']}: {p['navigators']} navigators on the path > {budget['max_path_navigators']}"
        for n in big:
            if n['routes'] > budget['max_tab_routes_above_nested']:
                out[f"tabs:{n['id']}"] = f"{n['label']}: {n['routes']} routes above {len(n['children'])} nested navigators > {budget['max_tab_routes_above_nested']}"
        if rules['inline_component']:
            for fl in scan.get('flags', []):
                m = re.match(r'INLINE COMPONENT: (\S+?):\d+ .*?for "([^"]+)"', fl)
                if m:
                    out[f'inline:{m[1]}:{m[2]}'] = fl
    if cfg.get('imports'):
        for e_from, e_to in edges_of(repo, edges_path):
            for r in cfg['imports']:
                src, exc, nev, allow = (matcher(r.get(k, [])) for k in ('from', 'except', 'never', 'allow'))
                if src(e_from) and not exc(e_from) and nev(e_to) and not allow(e_to):
                    out[f"import:{r['name']}:{e_from} -> {e_to}"] = r.get('why', '')
    files = list(source_files(repo)) if cfg.get('writers') or rules['scrollview_map'] else []
    texts = {f: open(os.path.join(repo, f), errors='ignore').read() for f in files}
    for r in cfg.get('writers', []):
        only = matcher(r.get('only', []))
        for f, t in texts.items():
            if not only(f) and re.search(r['calls'], t) and re.search(r['mentions'], t):
                out[f"writer:{r['name']}:{f}"] = r.get('why', '')
    if rules['scrollview_map']:
        for f, t in texts.items():
            for blk in re.finditer(r'<((?:[\w.]+\.)?ScrollView)\b[\s\S]*?</\1>', t):
                if '.map(' in blk[0]:
                    out[f'scrollview-map:{f}'] = f'.map( inside <ScrollView> at line {t[:blk.start()].count(chr(10)) + 1}: virtualize (FlatList/FlashList)'
                    break
    return vals, out


def check(repo, cfg, a, opt):
    vals, out = measure(repo, cfg, opt('--navscan'), opt('--edges'))
    accepted = set(cfg.get('accepted', []))
    new = {k: v for k, v in out.items() if k not in accepted}
    gone = sorted(accepted - set(out))
    budget = {**DEFAULT_BUDGET, **cfg.get('budget', {})}
    print(f'fitness {repo}: ' + ', '.join(f'{k} {vals[k]} (budget {budget[k]})' for k in vals))
    print(f'  {len(out)} violations: {len(out) - len(new)} accepted, {len(new)} new; {len(gone)} accepted ones fixed')
    for k, v in sorted(new.items()):
        print(f'  NEW  {k}  {v}')
    for k in gone[:20]:
        print(f'  fixed (tighten: init again)  {k}')
    if '--json' in a:
        json.dump({'values': vals, 'violations': out, 'new': sorted(new), 'fixed': gone}, open(opt('--json'), 'w'), indent=1)
    return 1 if new else 0


def init(repo, a, opt):
    path = opt('--config', os.path.join(repo, 'navlab.fitness.json'))
    cfg = json.load(open(path)) if os.path.exists(path) else {}
    for d in [a[i + 1] for i, x in enumerate(a) if x == '--design']:
        des = json.load(open(d))
        for key in ('imports', 'writers'):
            have = {r['name'] for r in cfg.get(key, [])}
            cfg.setdefault(key, []).extend(r for r in des.get('fitness', {}).get(key, []) if r['name'] not in have)
    vals, out = measure(repo, cfg, opt('--navscan'), opt('--edges'))
    # ratchet: each budget is today's value (a default the app already beats would let it regress unnoticed, gap g0049);
    # violations that exist today are accepted, new ones fail
    cfg['budget'] = {k: vals.get(k, v) for k, v in {**DEFAULT_BUDGET, **cfg.get('budget', {})}.items()}
    vals, out = measure(repo, cfg, opt('--navscan'), opt('--edges'))
    cfg['accepted'] = sorted(out)
    json.dump(cfg, open(path, 'w'), indent=1)
    print(f'init {path}: budget {cfg["budget"]}, {len(cfg.get("imports", []))} import rules, {len(cfg.get("writers", []))} writer rules, {len(out)} accepted violations')


def selftest():
    """Fixture arch/fixtures/fitness: a forbidden import, a state writer outside its owner, .map in a ScrollView, a big
    tab navigator above nested stacks, an inline component and a lag over budget. Each must be caught; accepting them
    passes; a new one after init fails."""
    fx = os.path.join(HERE, 'fixtures', 'fitness')
    scan = os.path.join(fx, 'navscan.json')
    cfg = json.load(open(os.path.join(fx, 'navlab.fitness.json')))
    vals, out = measure(fx, cfg, scan)
    want = {'import:ui-never-sync:src/ui/List.tsx -> src/sync/bus.ts', 'writer:convo-one-owner:src/ui/List.tsx',
            'scrollview-map:src/ui/List.tsx', 'tabs:nav.tsx:10', 'inline:nav.tsx:Feed', 'lag:nav.tsx:20'}
    assert want <= set(out), f'missing {want - set(out)}; got {sorted(out)}'
    assert 'writer:convo-one-owner:src/store/convo.ts' not in out and 'import:ui-never-sync:src/store/convo.ts -> src/sync/bus.ts' not in out, out
    assert vals == {'max_lag_ms': 20.0, 'max_path_navigators': 2, 'max_tab_routes_above_nested': 8}, vals
    noop = lambda k, d=None: {'--navscan': scan}.get(k, d)
    assert check(fx, cfg, [], noop) == 1
    assert check(fx, {**cfg, 'accepted': sorted(out)}, [], noop) == 0, 'accepted violations pass (ratchet)'
    assert check(fx, {**cfg, 'accepted': sorted(set(out) - {'scrollview-map:src/ui/List.tsx'})}, [], noop) == 1, 'a new one fails'
    # init ratchets: budgets become today's values, today's violations are accepted, the result passes
    import shutil
    tmp = os.path.join(tempfile.mkdtemp(), 'fx')
    shutil.copytree(fx, tmp)
    init(tmp, ['--navscan', scan], lambda k, d=None: {'--navscan': scan}.get(k, d))
    got = json.load(open(os.path.join(tmp, 'navlab.fitness.json')))
    assert got['budget'] == {'max_lag_ms': 20.0, 'max_tab_routes_above_nested': 8, 'max_path_navigators': 2}, got['budget']
    assert check(tmp, got, [], noop) == 0 and len(got['accepted']) == 4, got['accepted']  # budgets absorb lag/tabs; 4 item violations stay
    print('selftest ok')


def main():
    a = sys.argv[1:]
    if a[:1] == ['--selftest']:
        return selftest()
    if len(a) < 2 or a[0] not in ('check', 'init'):
        sys.exit(__doc__)
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    repo = os.path.abspath(a[1])
    if a[0] == 'init':
        return init(repo, a, opt)
    cfg_path = opt('--config', os.path.join(repo, 'navlab.fitness.json'))
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    sys.exit(check(repo, cfg, a, opt))


if __name__ == '__main__':
    main()
