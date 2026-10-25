#!/usr/bin/env python3
"""cochange: how one feature actually changes, from git history (read-only).

  cochange.py <repo> --scope depgraph.json [--since "3 years ago"] [--min-shared 3] [--json out.json]

- hotspots: commits x LOC per owned file, with bug-fix commits (message heuristics); where change and risk concentrate.
- change clusters: files that change together (coupling degree = shared commits / min(commits of the two files)),
  grouped by label propagation. Clusters that span several directories, or directories split across clusters, are
  where the folder structure and the real boundaries disagree.
- hidden coupling: files OUTSIDE the feature that keep changing with it (a boundary the import graph doesn't show).
Commits touching > 40 files are skipped (formatting, codemods, mass renames): they couple everything to everything.
"""
import json, os, re, subprocess, sys
from collections import Counter, defaultdict

BUGFIX = re.compile(r'\b(fix|bug|hotfix|crash|regression|broken|issue|revert)\b', re.I)


def git_commits(repo, since, paths=None, renames=True):
    """renames=True: rename detection (-M), and every path in an older commit is mapped to the file's current name, so a
    file's history survives a move (gap g0033). renames=False keeps the paths as they were at each commit (git show)."""
    fmt = ['--name-status', '-M'] if renames else ['--name-only']
    cmd = ['git', '-C', repo, 'log', '--no-merges', *fmt, f'--since={since}', '--pretty=format:@@%H\t%at\t%s']
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    commits, now = [], {}  # now: old path -> current path (log runs newest first, so later renames are already known)
    for block in out.split('@@')[1:]:
        lines = block.strip().split('\n')
        h, t, msg = (lines[0].split('\t') + ['', ''])[:3]
        files = []
        for l in lines[1:]:
            if not l.strip():
                continue
            if not renames:
                files.append(l)
                continue
            st, *ps = l.split('\t')
            cur = now.get(ps[-1], ps[-1])
            if st.startswith('R') and len(ps) == 2:
                now[ps[0]] = cur
            files.append(cur)
        commits.append({'hash': h, 'time': int(t or 0), 'msg': msg, 'files': list(dict.fromkeys(files))})
    return commits


def history(repo, since, renames=True):
    """Commits (paths relative to `repo`) from the repo that holds the real history. Handles an app below the git root
    (monorepo: paths carry a prefix) and a nested .git with almost no history inside a parent repo that tracks the folder
    (seen: mobile/.git with one commit). Commits touching > 40 files are dropped (codemods, formatting, renames)."""
    def top_of(d):
        return subprocess.run(['git', '-C', d, 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
    repo = os.path.realpath(repo)  # git reports real paths; a symlinked repo path (/tmp, /var, ~/.claude) breaks relpath
    top = top_of(repo)
    if not top:
        return None
    n = int(subprocess.run(['git', '-C', top, 'rev-list', '--count', 'HEAD'], capture_output=True, text=True).stdout.strip() or 0)
    parent = top_of(os.path.dirname(top)) if n < 5 else ''
    if parent and subprocess.run(['git', '-C', parent, 'ls-files', '--', os.path.relpath(repo, parent)], capture_output=True, text=True).stdout.strip():
        top = parent
    prefix = os.path.relpath(repo, top)
    strip = (lambda f: f[len(prefix) + 1:] if f.startswith(prefix + '/') else '../' + f) if prefix != '.' else (lambda f: f)
    return [dict(c, files=[strip(f) for f in c['files']]) for c in git_commits(top, since, renames=renames) if len(c['files']) <= 40]


def label_propagation(nodes, w):
    """Deterministic label propagation on a weighted graph: each node takes the label with the most weight among its
    neighbours (ties -> smallest label), until stable."""
    label = {n: n for n in nodes}
    for _ in range(50):
        moved = False
        for n in sorted(nodes):
            score = Counter()
            for m, x in w[n].items():
                score[label[m]] += x
            if not score:
                continue
            best = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            if best != label[n] and score[best] > score.get(label[n], 0):
                label[n], moved = best, True
        if not moved:
            break
    groups = defaultdict(list)
    for n, l in label.items():
        groups[l].append(n)
    return sorted(groups.values(), key=len, reverse=True)


def main():
    a = sys.argv[1:]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    repo = os.path.abspath(a[0])
    scope = json.load(open(opt('--scope', 'depgraph.json')))
    owned = {o['file']: o['loc'] for o in scope['owned']}
    since, min_shared = opt('--since', '3 years ago'), int(opt('--min-shared', '3'))
    commits = history(repo, since)
    if commits is None:
        print(f'{repo} is not a git repository: cochange needs history'); return
    touching = [c for c in commits if any(f in owned for f in c['files'])]
    if not touching:
        print(f'no commits touching the {len(owned)} owned files since {since} (renamed paths? try --since or check git history)')
        return
    n_commits, bug = Counter(), Counter()
    pair, ext = Counter(), Counter()
    for c in touching:
        mine = sorted({f for f in c['files'] if f in owned})
        others = {f for f in c['files'] if f not in owned and re.search(r'\.[jt]sx?$', f) and not re.search(r'test|spec|snap', f)}
        isbug = bool(BUGFIX.search(c['msg']))
        for f in mine:
            n_commits[f] += 1
            bug[f] += isbug
        for i, f in enumerate(mine):
            for g in mine[i + 1:]:
                pair[(f, g)] += 1
        for o in others:
            ext[o] += 1
    # coupling degree per pair
    w = defaultdict(dict)
    strong = []
    for (f, g), s in pair.items():
        if s < min_shared:
            continue
        deg = s / min(n_commits[f], n_commits[g])
        if deg >= 0.3:
            w[f][g] = w[g][f] = deg
            strong.append((deg, s, f, g))
    nodes = [f for f in n_commits if f in w]
    clusters = label_propagation(nodes, w)
    hot = sorted(n_commits, key=lambda f: -(n_commits[f] * owned[f]))
    span = [(c, sorted({os.path.dirname(f) for f in c})) for c in clusters if len(c) > 1]
    dir_clusters = defaultdict(set)
    for i, c in enumerate(clusters):
        for f in c:
            dir_clusters[os.path.dirname(f)].add(i)
    first, last = min(c['time'] for c in touching), max(c['time'] for c in touching)
    import time as _t
    print(f'cochange: {len(touching)} commits touch the feature ({sum(bug.values())} file-touches in bug-fix commits), '
          f'{_t.strftime("%Y-%m", _t.localtime(first))}..{_t.strftime("%Y-%m", _t.localtime(last))}; {len(owned)} owned files, {len(n_commits)} changed')
    print('\nhotspots (commits x LOC; bug-fix commits):')
    for f in hot[:12]:
        print(f'  {n_commits[f]:4} commits {owned[f]:5} loc {bug[f]:3} fixes  {f}')
    print(f'\nchange clusters (files that change together, coupling >= 0.3, >= {min_shared} shared commits):')
    for c, dirs in span[:10]:
        print(f'  {len(c)} files across {len(dirs)} dir(s): {", ".join(dirs[:4])}{" ..." if len(dirs) > 4 else ""}')
    split = {d: len(ix) for d, ix in dir_clusters.items() if len(ix) > 1}
    if split:
        print('\ndirectories split across change clusters (one folder, several reasons to change):')
        for d, k in sorted(split.items(), key=lambda kv: -kv[1])[:8]:
            print(f'  {k} clusters  {d}')
    print('\nhidden coupling: files outside the feature that change with it most often:')
    for f, k in ext.most_common(12):
        print(f'  {k:4} co-commits  {f}')
    never = [f for f in owned if f not in n_commits]
    if never:
        print(f'\n{len(never)} owned files unchanged since {since} (stable, or dead: check navscan reachability)')
    if '--json' in a:
        json.dump({'commits': len(touching), 'hotspots': [{'file': f, 'commits': n_commits[f], 'loc': owned[f], 'fixes': bug[f]} for f in hot],
                   'clusters': clusters, 'strong_pairs': sorted(strong, reverse=True)[:200], 'hidden_coupling': ext.most_common(50),
                   'unchanged': never}, open(opt('--json', 'cochange.json'), 'w'), indent=1)


def selftest():
    """Pins gap g0006: app in a monorepo subdir, and a near-empty nested .git inside a parent repo that tracks it."""
    import tempfile
    g = lambda d, *a: subprocess.run(['git', '-C', d, *a], capture_output=True, check=True)
    root = tempfile.mkdtemp()
    g(root, 'init', '-q'); g(root, 'config', 'user.email', 't@t'); g(root, 'config', 'user.name', 't')
    app = os.path.join(root, 'app'); os.makedirs(os.path.join(app, 'src'))
    for i in range(6):
        open(os.path.join(app, 'src', f'f{i % 2}.ts'), 'w').write(str(i)); open(os.path.join(root, 'README'), 'w').write(str(i))
        g(root, 'add', '-A'); g(root, 'commit', '-qm', f'c{i}')
    h = history(app, '10 years ago')
    assert len(h) == 6 and h[0]['files'] == ['../README', 'src/f1.ts'], f'monorepo prefix stripped, symlinked tmp path resolved: {h[0]}'
    g(app, 'init', '-q'); g(app, 'config', 'user.email', 't@t'); g(app, 'config', 'user.name', 't')
    g(app, 'add', '-A'); g(app, 'commit', '-qm', 'nested')
    assert len(history(app, '10 years ago')) == 6, 'nested near-empty .git falls back to the parent history'
    # g0033: a moved file keeps its history under its current name
    r = tempfile.mkdtemp()
    g(r, 'init', '-q'); g(r, 'config', 'user.email', 't@t'); g(r, 'config', 'user.name', 't')
    os.makedirs(os.path.join(r, 'old')); open(os.path.join(r, 'old', 'a.ts'), 'w').write('x\n' * 20)
    g(r, 'add', '-A'); g(r, 'commit', '-qm', 'add')
    open(os.path.join(r, 'old', 'a.ts'), 'a').write('y\n'); g(r, 'commit', '-qam', 'edit')
    os.makedirs(os.path.join(r, 'new')); g(r, 'mv', 'old/a.ts', 'new/a.ts'); g(r, 'commit', '-qm', 'move')
    g(r, 'mv', 'new/a.ts', 'new/b.ts'); g(r, 'commit', '-qm', 'rename')
    h = history(r, '10 years ago')
    assert all(c['files'] == ['new/b.ts'] for c in h) and len(h) == 4, f'renames followed to the current path: {h}'
    assert history(r, '10 years ago', renames=False)[-1]['files'] == ['old/a.ts'], 'renames=False keeps paths as committed'
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:2] == ['--selftest'] else main()
