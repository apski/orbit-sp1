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


def git_commits(repo, since, paths=None):
    cmd = ['git', '-C', repo, 'log', '--no-merges', '--name-only', f'--since={since}', '--pretty=format:@@%H\t%at\t%s']
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    commits = []
    for block in out.split('@@')[1:]:
        lines = block.strip().split('\n')
        h, t, msg = (lines[0].split('\t') + ['', ''])[:3]
        files = [l for l in lines[1:] if l.strip()]
        commits.append({'hash': h, 'time': int(t or 0), 'msg': msg, 'files': files})
    return commits


def history(repo, since):
    """Commits (paths relative to `repo`) from the repo that holds the real history. Handles an app below the git root
    (monorepo: paths carry a prefix) and a nested .git with almost no history inside a parent repo that tracks the folder
    (seen: mobile/.git with one commit). Commits touching > 40 files are dropped (codemods, formatting, renames)."""
    def top_of(d):
        return subprocess.run(['git', '-C', d, 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
    top = top_of(repo)
    if not top:
        return None
    n = int(subprocess.run(['git', '-C', top, 'rev-list', '--count', 'HEAD'], capture_output=True, text=True).stdout.strip() or 0)
    parent = top_of(os.path.dirname(top)) if n < 5 else ''
    if parent and subprocess.run(['git', '-C', parent, 'ls-files', '--', os.path.relpath(repo, parent)], capture_output=True, text=True).stdout.strip():
        top = parent
    prefix = os.path.relpath(repo, top)
    strip = (lambda f: f[len(prefix) + 1:] if f.startswith(prefix + '/') else '../' + f) if prefix != '.' else (lambda f: f)
    return [dict(c, files=[strip(f) for f in c['files']]) for c in git_commits(top, since) if len(c['files']) <= 40]


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


if __name__ == '__main__':
    main()
