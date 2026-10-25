#!/usr/bin/env python3
"""replay: backtest candidate designs against the feature's real change history (the outside judge).

  replay.py <repo> --scope depgraph.json --map designA.json [--map designB.json ...] [--since "3 years ago"]

A design maps files to modules: {"name": "A: vertical slices", "modules": {"payments": ["src/views/Repayment/**", ...]},
"deleted": ["glob", ...]}. Files outside the feature that the design absorbs (hidden coupling partners) can be listed
too; they join the replay scope. Every scope file must land in a module or in "deleted" (unmapped files are listed).
The as-is layout is "module = directory". For every past commit touching the scope it counts the modules that commit
would have touched under each layout. Good boundaries keep changes local: fewer modules per commit, more single-module
commits, fewer cross-module import edges. Caveat: history favours the axes change took in the past. Pair this with
scenario walkthroughs for the roadmap.
"""
import fnmatch, json, os, statistics as st, sys
from pathlib import PurePosixPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cochange import history  # noqa: E402


def matcher(globs):
    def m(f):
        p = PurePosixPath(f)
        for g in globs:
            try:
                if p.full_match(g):
                    return True
            except AttributeError:  # Python < 3.13
                if fnmatch.fnmatch(f, g.replace('**/', '*').replace('**', '*')):
                    return True
        return False
    return m


def module_of(design):
    rules = [(name, matcher(g)) for name, g in design.get('modules', {}).items()]
    deleted = matcher(design.get('deleted', []))

    def f(path):
        if deleted(path):
            return '_deleted'
        for name, m in rules:
            if m(path):
                return name
        return None
    return f


def score(commits, scope, mod, edges):
    per, unmapped = [], set()
    for c in commits:
        mine = [f for f in c['files'] if f in scope]
        if not mine:
            continue
        ms = set()
        for f in mine:
            m = mod(f)
            if m is None:
                unmapped.add(f)
                m = '?' + f
            if m != '_deleted':
                ms.add(m)
        if ms:
            per.append(len(ms))
    cross = sum(1 for a, b in edges if a in scope and b in scope and mod(a) not in (None, '_deleted') and mod(b) not in (None, '_deleted') and mod(a) != mod(b))
    mods = {mod(f) for f in scope} - {None, '_deleted'}
    return {'commits': len(per), 'modules': len(mods), 'mean_modules_per_commit': round(st.mean(per), 2) if per else None,
            'p90': sorted(per)[int(0.9 * (len(per) - 1))] if per else None,
            'single_module_commits': round(sum(1 for x in per if x == 1) / len(per), 2) if per else None,
            'cross_module_imports': cross, 'unmapped': sorted(unmapped)}


def main():
    a = sys.argv[1:]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    maps = [a[i + 1] for i, x in enumerate(a) if x == '--map']
    repo = os.path.abspath(a[0])
    dep = json.load(open(opt('--scope', 'depgraph.json')))
    scope = {o['file'] for o in dep['owned']}
    designs = [json.load(open(m)) for m in maps]
    commits = history(repo, opt('--since', '3 years ago')) or []
    # designs may absorb files from outside the feature (hidden coupling partners): they join the scope
    every = set(f for c in commits for f in c['files'])
    for d in designs:
        for g in d.get('modules', {}).values():
            m = matcher(g)
            scope |= {f for f in every if m(f) and os.path.exists(os.path.join(repo, f))}
    edges = [tuple(e) for e in dep.get('edges', [])]
    rows = [('as-is (module = directory)', score(commits, scope, lambda f: os.path.dirname(f), edges))]
    rows += [(d.get('name', m), score(commits, scope, module_of(d), edges)) for d, m in zip(designs, maps)]
    print(f'replay: {len(scope)} files in scope, history since {opt("--since", "3 years ago")}\n')
    print(f'{"layout":<44} {"modules":>7} {"commits":>7} {"mods/commit":>11} {"p90":>4} {"single-module":>13} {"x-module imports":>16}')
    for name, r in rows:
        print(f'{name[:44]:<44} {r["modules"]:>7} {r["commits"]:>7} {r["mean_modules_per_commit"]!s:>11} {r["p90"]!s:>4} {r["single_module_commits"]!s:>13} {r["cross_module_imports"]:>16}')
    for name, r in rows[1:]:
        if r['unmapped']:
            print(f'\n{name}: {len(r["unmapped"])} scope files not placed in any module (design incomplete): {", ".join(r["unmapped"][:6])}')


if __name__ == '__main__':
    main()
