#!/usr/bin/env python3
"""portfolio: every feature of an RN app on one evidence table, with the move each one needs.

  portfolio.py <repo> [--unit navigators|screens] [--since "1 year ago"] [--usage usage.json] [--model model.json] [--json out.json]

Features = the navigators the app actually reaches (navscan), or with --unit screens the screens grouped by feature folder
(src/screens/<Feature>/..., else the screen file's parent folder). Use screens when every tab registers the same shared
screen list (one navigator == the whole app). Per feature:
  structure  exclusively owned files/LOC (depgraph; nested navigators' files belong to the child), directories spanned
  history    commits and bug-fix commits in the window (git), i.e. change pressure and risk
  perf       worst predicted RN lag vs native per push on paths ending in it (NavLab model)
  usage      optional {"RouteName": views} (analytics export) summed over the navigator's screens
Suggested move (transparent rules, the steward skill decides):
  navlab-audit   predicted lag >= 1 frame (16.7ms)
  design-afresh  hotspot (top-quartile commits x LOC) and scattered (>= 4 dirs) or fix share >= 30%
  check-dead     unchanged in the window and no usage
  watch          everything else
"""
import json, os, statistics as st, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from cochange import BUGFIX, history  # noqa: E402


def main():
    a = sys.argv[1:]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    repo = os.path.abspath(a[0])
    since = opt('--since', '1 year ago')
    tmp = tempfile.mkdtemp()
    scan_json = os.path.join(tmp, 'navscan.json')
    cmd = ['node', os.path.join(ROOT, 'scan', 'navscan.mjs'), repo, '--json', scan_json]
    if '--model' in a:
        cmd += ['--model', opt('--model', '')]
    subprocess.run(cmd, capture_output=True)
    scan = json.load(open(scan_json))
    nodes = {n['id']: n for n in scan['tree']}
    feats = [n for n in scan['tree'] if n.get('reached') is not False]
    unit = opt('--unit', 'navigators')
    if unit == 'screens':
        groups = {}
        reached_screens = {}
        for n in feats:
            for name, f in zip(n['screens'], n.get('screenFiles', [])):
                if not f:
                    continue
                parts = f.split('/')
                key = parts[parts.index('screens') + 1] if 'screens' in parts[:-1] else parts[-2]
                key = os.path.splitext(key)[0]
                groups.setdefault(key, set()).add(f)
                reached_screens.setdefault(key, set()).add(name)
        feats = [{'id': k, 'label': k, 'kind': 'screens', 'routes': len(reached_screens[k]), 'children': [], 'screens': sorted(reached_screens[k])} for k in groups]
        nodes = {n['id']: n for n in feats}
        names = {n['id']: n['id'] for n in feats}
        spec_d = {k: sorted(v) for k, v in groups.items()}
    else:
        names = {}
        for n in feats:  # readable unique name: "<label> @<file>"
            names[n['id']] = f"{n['label']} @{n['id'].split(':')[0].split('/')[-1]}"
        spec_d = {names[n['id']]: [n['id'].rsplit(':', 1)[0]] for n in feats}
    spec = os.path.join(tmp, 'features.json')
    json.dump(spec_d, open(spec, 'w'))
    dep_json = os.path.join(tmp, 'dep.json')
    subprocess.run(['node', os.path.join(HERE, 'depgraph.mjs'), repo, '--features', spec, '--json', dep_json], capture_output=True)
    dep = json.load(open(dep_json))

    # exclusive ownership: a file owned by a nested navigator's feature belongs to the child, not the parent
    def descendants(i, seen=None):
        seen = seen or set()
        for c in nodes[i]['children']:
            if c not in seen:
                seen.add(c)
                descendants(c, seen)
        return seen
    owned = {}
    for n in feats:
        mine = {o['file']: o.get('loc', 0) for o in dep[names[n['id']]]['owned']}
        for d in descendants(n['id']):
            if d in names:
                for o in dep[names[d]]['owned']:
                    mine.pop(o['file'], None)
        owned[n['id']] = mine

    # history, parsed once
    commits = history(repo, since) or []
    usage = json.load(open(opt('--usage', ''))) if '--usage' in a else {}

    rows = []
    for n in feats:
        files = owned[n['id']]
        touching = [c for c in commits if any(f in files for f in c['files'])]
        fixes = sum(1 for c in touching if BUGFIX.search(c['msg']))
        lags = [p['lag'] for p in scan['paths'] if p.get('lag') is not None and (p['ids'][-1] == n['id'] or unit == 'screens')]
        rows.append({'feature': names[n['id']], 'id': n['id'], 'kind': n['kind'], 'routes': n['routes'],
                     'files': len(files), 'loc': sum(files.values()), 'dirs': len({os.path.dirname(f) for f in files}),
                     'commits': len(touching), 'fixes': fixes, 'fix_share': round(fixes / len(touching), 2) if touching else 0,
                     'lag_ms': round(max(lags), 1) if lags else None,
                     'usage': sum(usage.get(s, 0) for s in n['screens']) if usage else None})
    hot = [r['commits'] * r['loc'] for r in rows if r['commits']]
    q3 = sorted(hot)[int(0.75 * (len(hot) - 1))] if hot else 0
    for r in rows:
        score = r['commits'] * r['loc']
        if r['lag_ms'] is not None and r['lag_ms'] >= 16.7:
            r['move'] = 'navlab-audit'
        elif score and score >= q3 and (r['dirs'] >= 4 or r['fix_share'] >= 0.3):
            r['move'] = 'design-afresh'
        elif commits and r['commits'] == 0 and not r['usage'] and not nodes.get(r['id'], {}).get('children'):
            r['move'] = 'check-dead'
        else:
            r['move'] = 'watch'
        r['hotspot'] = score
    rows.sort(key=lambda r: (-(r['move'] in ('navlab-audit', 'design-afresh')), -r['hotspot'], -(r['lag_ms'] or 0)))

    print(f'portfolio {repo}: {len(rows)} features (reached navigators), history since {since}: {len(commits)} commits'
          f'{", usage joined" if usage else ""}\n')
    print(f'{"feature":<46} {"kind":<5} {"routes":>6} {"files":>5} {"loc":>6} {"dirs":>4} {"commits":>7} {"fixes":>5} {"lag ms":>6}  move')
    for r in rows:
        print(f'{r["feature"][:46]:<46} {r["kind"]:<5} {r["routes"]:>6} {r["files"]:>5} {r["loc"]:>6} {r["dirs"]:>4} {r["commits"]:>7} {r["fixes"]:>5} '
              f'{r["lag_ms"] if r["lag_ms"] is not None else "-":>6}  {r["move"]}')
    if '--json' in a:
        json.dump({'repo': repo, 'since': since, 'features': rows}, open(opt('--json', 'portfolio.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
