#!/usr/bin/env python3
"""replay: backtest candidate designs against the feature's real change history (the outside judge).

  replay.py <repo> --scope depgraph.json --map designA.json [--map designB.json ...] [--since "3 years ago"]
  replay.py --selftest

A design maps files to modules: {"name": "A: vertical slices", "modules": {"payments": ["src/views/Repayment/**", ...]},
"deleted": ["glob", ...]}. Files outside the feature that the design absorbs (hidden coupling partners) can be listed
too; they join the replay scope. Every scope file must land in a module or in "deleted" (unmapped files are listed).
The as-is layout is "module = directory". For every past commit touching the scope it counts the modules that commit
would have touched under each layout. Good boundaries keep changes local: fewer modules per commit, more single-module
commits, fewer cross-module import edges. Caveat: history favours the axes change took in the past. Pair this with
scenario walkthroughs for the roadmap.
"""
import fnmatch, json, os, random, statistics as st, sys
from pathlib import PurePosixPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cochange import history  # noqa: E402


def matcher(globs):
    exact = {g for g in globs if not any(ch in g for ch in '*?[')}  # file-level maps: a set lookup, not a glob match per path
    globs = [g for g in globs if g not in exact]

    def m(f):
        if f in exact:
            return True
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


def score(commits, scope, mod, edges, rounds=20):
    """Locality of one layout. `lift` = mean modules/commit of random layouts with the same module sizes (files shuffled
    between modules) divided by the layout's own: > 1 means the boundaries follow how the code changes, beyond what
    coarseness alone buys (fewer, bigger modules always touch fewer modules per commit; gap g0042)."""
    assign = {f: mod(f) for f in scope}
    unmapped = sorted(f for f, m in assign.items() if m is None)
    touched = [[f for f in c['files'] if f in scope] for c in commits]
    touched = [t for t in touched if t]

    def per_commit(lab):
        out = []
        for t in touched:
            ms = {lab[f] if lab[f] is not None else '?' + f for f in t} - {'_deleted'}
            if ms:
                out.append(len(ms))
        return out
    per = per_commit(assign)
    placed = [f for f, m in assign.items() if m not in (None, '_deleted')]
    rng, null = random.Random(7), []
    for _ in range(rounds if per else 0):
        labels = [assign[f] for f in placed]
        rng.shuffle(labels)
        n = per_commit({**assign, **dict(zip(placed, labels))})
        null.append(st.mean(n))
    cross = sum(1 for a, b in edges if a in scope and b in scope and assign[a] not in (None, '_deleted') and assign[b] not in (None, '_deleted') and assign[a] != assign[b])
    mods = {m for m in assign.values()} - {None, '_deleted'}
    mean = st.mean(per) if per else None
    return {'commits': len(per), 'modules': len(mods), 'mean_modules_per_commit': round(mean, 2) if per else None,
            'p90': sorted(per)[int(0.9 * (len(per) - 1))] if per else None,
            'single_module_commits': round(sum(1 for x in per if x == 1) / len(per), 2) if per else None,
            'null_mean': round(st.mean(null), 2) if null else None, 'lift': round(st.mean(null) / mean, 2) if null and mean else None,
            'cross_module_imports': cross, 'unmapped': unmapped}


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
    print(f'{"layout":<44} {"modules":>7} {"commits":>7} {"mods/commit":>11} {"random":>6} {"lift":>5} {"p90":>4} {"single-module":>13} {"x-module imports":>16}')
    for name, r in rows:
        print(f'{name[:44]:<44} {r["modules"]:>7} {r["commits"]:>7} {r["mean_modules_per_commit"]!s:>11} {r["null_mean"]!s:>6} {r["lift"]!s:>5} {r["p90"]!s:>4} {r["single_module_commits"]!s:>13} {r["cross_module_imports"]:>16}')
    print('\nrandom = same module sizes, files shuffled; lift = random / mods/commit (> 1: boundaries follow change, beyond coarseness)')
    for name, r in rows[1:]:
        if r['unmapped']:
            print(f'\n{name}: {len(r["unmapped"])} scope files not placed in any module (design incomplete): {", ".join(r["unmapped"][:6])}')


def selftest():
    """Pins g0038: exact-path maps match by set lookup (fast) and globs still match."""
    import time
    m = matcher([f'src/f{i}.ts' for i in range(500)] + ['lib/**/*.ts'])
    t = time.process_time()
    hits = sum(m(f'src/f{i}.ts') for i in range(20000)) + sum(m(f'other/x{i}.ts') for i in range(20000))
    assert hits == 500 and m('lib/a/b.ts') and not m('lib/a/b.tsx'), hits
    assert time.process_time() - t < 2, f"matcher too slow: {time.process_time() - t:.1f}s  CPU for 40k paths x 500 exact globs"
    # g0042: same module sizes, cohesive vs split boundaries -> lift separates them; mods/commit alone would not for coarse layouts
    commits = [{'files': ['a1', 'a2']}, {'files': ['b1', 'b2']}] * 5
    scope = {'a1', 'a2', 'b1', 'b2'}
    good = score(commits, scope, {'a1': 'A', 'a2': 'A', 'b1': 'B', 'b2': 'B'}.get, [])
    bad = score(commits, scope, {'a1': 'A', 'a2': 'B', 'b1': 'A', 'b2': 'B'}.get, [])
    assert good['lift'] > 1.2 and bad['lift'] < 1, (good, bad)
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:2] == ['--selftest'] else main()
