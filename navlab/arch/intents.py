#!/usr/bin/env python3
"""intents: real change intents for a feature, the test set for judging designs semantically.

  intents.py list <repo> --scope depgraph.json [--since "3 years ago"] [--issues issues.txt] [--json out]
  intents.py score answers.json

list: commit subjects that touched the feature (noise removed: merges, version bumps, formatting, lint, typos, wip,
renames-only), deduplicated, plus optional issue/roadmap titles (one per line). Hand these to a reviewer agent that
has NOT seen the design rationale, together with each candidate's feature model, and ask per intent: which modules
would this change touch, which contracts move?
score: answers.json = {"candidates": {"A": {"<intent>": ["mod1", "mod2"], ...}, "B": {...}}} ->
mean modules per intent, share of single-module intents, and the intents where candidates disagree most.
"""
import json, os, re, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cochange import history  # noqa: E402

NOISE = re.compile(r'^(merge|bump|release|version|v?\d+\.\d+|chore\(deps\)|format|prettier|lint|eslint|typo|wip|rename|move|cleanup|clean up|update snapshot|snapshots?)\b|^\W*$', re.I)


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if a[0] == 'score':
        ans = json.load(open(a[1]))['candidates']
        intents = sorted(set().union(*[set(v) for v in ans.values()]))
        print(f'{len(intents)} intents scored')
        for c, per in ans.items():
            n = [len(set(per.get(i, []))) for i in intents if i in per]
            print(f'  {c:<20} mean modules/intent {st.mean(n):.2f}   single-module {sum(x == 1 for x in n) / len(n):.0%}   n={len(n)}')
        spread = sorted(intents, key=lambda i: -(max(len(set(v.get(i, []))) for v in ans.values()) - min(len(set(v.get(i, []))) for v in ans.values())))
        print('  intents where candidates differ most: ' + '; '.join(spread[:5]))
        return
    repo = os.path.abspath(a[1])
    scope = {o['file'] for o in json.load(open(opt('--scope')))['owned']}
    seen, out = set(), []
    for c in history(repo, opt('--since', '3 years ago')) or []:
        mine = [f for f in c['files'] if f in scope]
        subj = re.sub(r'^\w+(\([^)]*\))?!?:\s*', '', c['msg']).strip()  # drop conventional-commit prefix
        if not mine or NOISE.search(subj) or subj.lower() in seen or len(subj) < 8:
            continue
        seen.add(subj.lower())
        out.append({'intent': subj, 'commit': c['hash'][:8], 'files_in_scope': len(mine)})
    if '--issues' in a:
        out += [{'intent': l.strip(), 'source': 'issue'} for l in open(opt('--issues')) if l.strip()]
    print(f'{len(out)} intents ({sum("commit" in x for x in out)} from history):')
    for x in out[:40]:
        print(f'  - {x["intent"][:100]}' + (f'   [{x["commit"]}, {x["files_in_scope"]} files]' if 'commit' in x else '   [issue]'))
    if '--json' in a:
        json.dump(out, open(opt('--json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
