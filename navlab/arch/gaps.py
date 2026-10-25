#!/usr/bin/env python3
"""gaps: the pack's learning loop. Every time a skill or tool falls short on a real repo, record it; rank what holds us
back; fix the top ones with a fixture + test so they never come back.

  gaps.py add --skill navscan --kind missing-pattern --impact degraded --repo social-app \
              --what "custom navigator via createNavigatorFactory not recognized" \
              --evidence "src/Navigation.tsx:167" --workaround "adapter.factories" [--fix-idea "..."]
  gaps.py list [--open] [--skill X]
  gaps.py rank                         backlog: clusters by (skill, kind, signature), scored frequency x impact
  gaps.py close <id> --fixed-by "<file or commit>" --test "<test that pins it>"
  gaps.py promote <id> | drop <id> --why "..."    triage a harvested candidate (drop = wontfix, kept so it isn't re-harvested)
  gaps.py harvest <repo>               turn a repo's .navlab artifacts into candidate gaps (unresolved scans, missing profile)
  gaps.py verify                       rerun every pinning test (the --test commands, from the pack root); a failure reopens its gaps
  gaps.py selftest

status:  candidate (harvested, untriaged) -> open -> unpinned (fixed, no test yet: debt) -> fixed (pinned by a test) | wontfix

kinds:   missing-pattern (code shape a tool can't read) | false-positive | false-negative | missing-tool | missing-skill
         | missing-data (an input the user/app lacks) | wrong-answer | slow | ux (hard to use / unclear output)
impact:  blocked (5) | wrong-answer (4) | degraded (2) | slow (1)
--repo is the repo directory's basename (what harvest uses), so one repo never counts as two. The log lives in the pack (learning/gaps.jsonl), not in target repos. Record repo by short name and file:line only;
never paste proprietary code. A gap is closed only with --test: a fixture or self-test that fails without the fix.
"""
import json, os, re, sys, time
from collections import defaultdict

PACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.environ.get('NAVLAB_GAPS', os.path.join(PACK, 'learning', 'gaps.jsonl'))
IMPACT = {'blocked': 5, 'wrong-answer': 4, 'degraded': 2, 'slow': 1}
KINDS = {'missing-pattern', 'false-positive', 'false-negative', 'missing-tool', 'missing-skill', 'missing-data', 'wrong-answer', 'slow', 'ux'}


def load():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def save(rows):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'w') as f:
        f.writelines(json.dumps(r) + '\n' for r in rows)


def next_id(rows):
    return f"g{max([int(r['id'][1:]) for r in rows] or [0]) + 1:04d}"


def signature(r):
    """Group the same gap seen in different repos: skill + kind + the 'what' with paths, quotes and numbers removed."""
    w = re.sub(r'`[^`]*`|"[^"]*"|\S+/\S+|\d+', '', r['what'].lower())
    return f"{r['skill']}|{r['kind']}|{' '.join(sorted(set(re.findall(r'[a-z]{4,}', w))))}"


def main():
    import fcntl  # several agents log gaps at once: hold the ledger lock for the whole load -> save
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    lock = open(LOG + '.lock', 'w')
    if sys.argv[1:2] != ['selftest']:
        fcntl.flock(lock, fcntl.LOCK_EX)
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    rows = load()
    cmd = a[0] if a else 'rank'
    if cmd == 'add':
        kind, impact = opt('--kind'), opt('--impact', 'degraded')
        assert kind in KINDS, f'kind must be one of {sorted(KINDS)}'
        assert impact in IMPACT, f'impact must be one of {list(IMPACT)}'
        r = {'id': next_id(rows), 'date': time.strftime('%Y-%m-%d'), 'skill': opt('--skill'), 'kind': kind, 'impact': impact,
             'repo': opt('--repo', ''), 'what': opt('--what'), 'evidence': opt('--evidence', ''), 'workaround': opt('--workaround', ''),
             'fix_idea': opt('--fix-idea', ''), 'status': 'open', 'fixed_by': opt('--fixed-by', ''), 'test': opt('--test', '')}
        if r['fixed_by']:
            r['status'] = 'fixed' if r['test'] else 'unpinned'
        r['signature'] = signature(r)
        dup = [x for x in rows if x['signature'] == r['signature'] and x['status'] in ('fixed', 'unpinned') and x['repo'] != r['repo']]
        rows.append(r)
        save(rows)
        print(f"{r['id']} {r['skill']} {r['kind']} ({r['impact']}): {r['what']}")
        if dup:
            print(f"  note: matches closed gap {dup[0]['id']} ({dup[0]['status']}): a regression, or the fix didn't generalize")
    elif cmd == 'close':
        r = next(x for x in rows if x['id'] == a[1])
        if not opt('--test'):
            sys.exit('close needs --test: the fixture or self-test that fails without the fix')
        r.update(status='fixed', fixed_by=opt('--fixed-by', ''), test=opt('--test'), closed=time.strftime('%Y-%m-%d'))
        save(rows)
        print(f"{r['id']} fixed by {r['fixed_by']}; pinned by {r['test']}")
    elif cmd in ('promote', 'drop'):
        r = next(x for x in rows if x['id'] == a[1])
        r['status'] = 'open' if cmd == 'promote' else 'wontfix'
        r['fix_idea'] = opt('--fix-idea', r['fix_idea'])
        if cmd == 'drop':
            r['why'] = opt('--why', '')
        save(rows)
        print(f"{r['id']} -> {r['status']}")
    elif cmd == 'verify':
        import subprocess
        tests = sorted({r['test'] for r in rows if r['status'] == 'fixed' and r['test']})
        failed = set()
        for t in tests:
            ok = subprocess.run(t, shell=True, cwd=PACK, capture_output=True).returncode == 0
            print(f"  {'ok  ' if ok else 'FAIL'} {t}")
            if not ok:
                failed.add(t)
        for r in rows:
            if r['test'] in failed and r['status'] == 'fixed':
                r['status'] = 'open'; r['regressed'] = time.strftime('%Y-%m-%d')
                print(f"  REGRESSION {r['id']} reopened: {r['what'][:80]}")
        save(rows)
        sys.exit(1 if failed else 0)
    elif cmd == 'selftest':
        selftest()
    elif cmd == 'list':
        for r in rows:
            if '--open' in a and r['status'] != 'open':
                continue
            if opt('--skill') and r['skill'] != opt('--skill'):
                continue
            print(f"{r['id']} {r['status']:<6} {r['skill']:<18} {r['kind']:<15} {r['impact']:<12} {r['repo']:<14} {r['what'][:90]}")
    elif cmd == 'rank':
        groups = defaultdict(list)
        for r in rows:
            groups[r['signature']].append(r)
        backlog = []
        for sig, rs in groups.items():
            open_ = [r for r in rs if r['status'] in ('open', 'unpinned')]
            if not open_:
                continue
            repos = {r['repo'] for r in rs}
            # unpinned = half weight: the bug is gone but nothing stops it coming back
            score = sum(IMPACT[r['impact']] * (0.5 if r['status'] == 'unpinned' else 1) for r in open_) * len(repos)
            backlog.append((score, len(open_), repos, open_[0]))
        backlog.sort(key=lambda x: -x[0])
        st = defaultdict(int)
        for r in rows:
            st[r['status']] += 1
        print(f"{len(rows)} gaps: {st['open']} open, {st['unpinned']} unpinned (fixed, no test), {st['fixed']} fixed+pinned, "
              f"{st['candidate']} candidates to triage, {st['wontfix']} wontfix")
        by_kind = defaultdict(int)
        for r in rows:
            by_kind[r['kind']] += 1
        print('  by kind: ' + ', '.join(f'{k} {v}' for k, v in sorted(by_kind.items(), key=lambda x: -x[1])))
        print('\nbacklog (score = impact x repos seen in):')
        for score, n, repos, r in backlog[:15]:
            tag = ' [needs test]' if r['status'] == 'unpinned' else ''
            print(f"  {score:5.1f}  {r['id']} {r['skill']:<14} {r['kind']:<15}{tag} x{n} in {', '.join(sorted(x for x in repos if x)) or '-'}: {r['what'][:80]}")
            if r['fix_idea']:
                print(f"       fix idea: {r['fix_idea'][:110]}")
        skills = [r for _, _, _, r in backlog if r['kind'] in ('missing-skill', 'missing-tool')]
        if skills:
            print('\nnew skill / tool candidates: ' + '; '.join(f"{r['what'][:60]} ({r['id']})" for r in skills[:5]))
    elif cmd == 'harvest':
        repo = os.path.abspath(a[1])
        nav = os.path.join(repo, '.navlab', 'navscan.json')
        name = os.path.basename(repo)
        new = 0
        if os.path.exists(nav):
            kinds = defaultdict(list)
            for u in json.load(open(nav)).get('unresolved', []):
                kinds[u['kind']].append(u)
            for k, us in kinds.items():
                if k in ('inline-component', 'parse-error'):
                    continue
                r = {'id': next_id(rows), 'date': time.strftime('%Y-%m-%d'), 'skill': 'navscan', 'kind': 'missing-pattern',
                     'impact': 'degraded', 'repo': name, 'what': f'navscan unresolved {k} x{len(us)}', 'evidence': us[0]['at'],
                     'workaround': us[0]['fix'], 'fix_idea': '', 'status': 'candidate', 'fixed_by': '', 'test': ''}
                r['signature'] = signature(r)
                if not any(x['signature'] == r['signature'] and x['repo'] == name for x in rows):
                    rows.append(r); new += 1
        cfg = os.path.join(repo, 'navlab.config.json')
        if os.path.exists(cfg):
            for sec, v in json.load(open(cfg)).get('profile', {}).items():
                if v.get('status') == 'missing' and sec in ('usage', 'telemetry', 'e2e', 'nodeModules', 'apiClients'):
                    r = {'id': next_id(rows), 'date': time.strftime('%Y-%m-%d'), 'skill': 'profile', 'kind': 'missing-data',
                         'impact': 'degraded', 'repo': name, 'what': f'repo has no {sec} input', 'evidence': 'navlab.config.json profile',
                         'workaround': '', 'fix_idea': '', 'status': 'candidate', 'fixed_by': '', 'test': ''}
                    r['signature'] = signature(r)
                    if not any(x['signature'] == r['signature'] and x['repo'] == name for x in rows):
                        rows.append(r); new += 1
        save(rows)
        print(f'harvested {new} candidate gap(s) from {name}: triage with `gaps.py list` then promote <id> [--fix-idea ...] / drop <id> --why ...')
    else:
        print(__doc__)


def selftest():
    import subprocess, tempfile
    os.environ['NAVLAB_GAPS'] = tmp = os.path.join(tempfile.mkdtemp(), 'g.jsonl')
    run = lambda *x: subprocess.run([sys.executable, __file__, *x], capture_output=True, text=True, env=os.environ).stdout
    run('add', '--skill', 'navscan', '--kind', 'missing-pattern', '--repo', 'a', '--what', 'custom navigator `X` not recognized', '--impact', 'blocked')
    out = run('add', '--skill', 'navscan', '--kind', 'missing-pattern', '--repo', 'b', '--what', 'custom navigator "Y" not recognized at src/a.tsx:4')
    assert 'note' not in out, 'open gap is not a regression'
    rows = [json.loads(l) for l in open(tmp)]
    assert rows[0]['signature'] == rows[1]['signature'], 'same gap in two repos clusters'
    assert 'x2 in a, b' in run('rank') and ' 14.0 ' in run('rank'), '(5 + 2) x 2 repos'
    assert run('close', 'g0001', '--fixed-by', 'f') == '', 'close refuses without --test'
    run('close', 'g0001', '--fixed-by', 'f', '--test', 't'); run('close', 'g0002', '--fixed-by', 'f', '--test', 't')
    assert 'note: matches closed gap' in run('add', '--skill', 'navscan', '--kind', 'missing-pattern', '--repo', 'c', '--what', 'custom navigator Z not recognized')
    run('add', '--skill', 'ds', '--kind', 'false-positive', '--repo', 'a', '--what', 'icon set', '--fixed-by', 'ds.py')
    assert '[needs test]' in run('rank'), 'fixed without a test stays on the backlog'
    print('selftest ok')


if __name__ == '__main__':
    main()
