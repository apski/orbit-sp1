"""Backtest: real fix commits from a repo's history become held-out tasks for comparing ways of working.

  mine     candidate fix commits -> tasks whose fix-commit tests fail on the parent and pass on the fix (fail-to-pass)
  describe a separate session writes a symptom-only task text per task; a second check rejects leaks of the fix
  run      one arm (oneshot | factory | bestof) on the task set, each task in its own worktree at the parent
  judge    the hidden judge: fix commit's F2P + P2P tests, on each arm's result
  score    paired comparison of arms (precision of "done", solve rate, cost), McNemar exact test

The repo is private, so the model can't have seen these fixes: cleaner held-out data than a public benchmark.
"""
import argparse, concurrent.futures as cf, json, random, re, threading
from pathlib import Path
import vy

TASKS = lambda prof: Path(prof['home']) / 'backtest' / 'tasks'


def candidates(prof, since, max_src):
    b = vy.bare(prof)
    out = vy.git(b, 'log', f'--since={since}', '--no-merges', '-i', '--grep=fix', '--format=@%H %P', '--name-status')
    cands, cur = [], None
    for line in out.splitlines() + ['@']:
        if line.startswith('@'):
            if cur and len(cur['parents']) == 1:
                src = [f for s, f in cur['files'] if f.startswith(prof['test_cwd'] + '/') and f.endswith('.py')
                       and not vy.is_test(prof, f) and s != 'D' and '/tests/' not in f]
                tests = [f for s, f in cur['files'] if vy.is_test(prof, f) and f.startswith(prof['test_cwd'] + '/') and s != 'D']
                other_code = [f for s, f in cur['files'] if not f.startswith(prof['test_cwd'] + '/')
                              and re.search(r'\.(py|ts|tsx|js|swift|rs|kt)$', f)]
                if tests and 1 <= len(src) <= max_src and not other_code:
                    cur.update(src=src, tests=tests,
                               support=[f for s, f in cur['files'] if f.startswith(prof['test_cwd'] + '/')
                                        and f not in src and s != 'D'])
                    cands.append(cur)
            if line == '@':
                break
            sha, *parents = line[1:].split()
            cur = {'fix': sha, 'parents': parents, 'files': []}
        elif line.strip() and cur is not None:
            parts = line.split('\t')
            cur['files'].append((parts[0][0], parts[-1]))
    return cands


_local = threading.local()


def _wt(prof):
    if not hasattr(_local, 'wt'):
        _local.wt = vy.worktree(prof, 'HEAD', Path(prof['home']) / 'wt' / f'mine-{threading.get_ident()}')
    return _local.wt


def at(wt, sha):
    vy.git(wt, 'checkout', '-q', '-f', sha)
    vy.git(wt, 'clean', '-q', '-fdx', '-e', '.venv')


def mine_one(prof, c):
    wt = _wt(prof)
    fix, base = c['fix'], c['parents'][0]
    at(wt, fix)
    r1, _, _ = vy.pytest(prof, wt, c['tests'], timeout=180)
    r2, _, _ = vy.pytest(prof, wt, c['tests'], timeout=180) if r1 else ({}, 0, '')
    stable_pass = {t for t in r1 if r1[t] == 'pass' and r2.get(t) == 'pass'}
    at(wt, base)
    vy.git(wt, 'checkout', '-q', fix, '--', *c['support'])  # the fix's tests + fixtures onto the parent's code
    rb, _, tail = vy.pytest(prof, wt, c['tests'], timeout=180) if stable_pass else ({}, 0, '')
    # a test absent at base didn't collect there (module import failed): it can't tell fixes apart, leave it out
    f2p = sorted(t for t in stable_pass if rb.get(t) in ('fail', 'error'))
    p2p = sorted(t for t in stable_pass if rb.get(t) == 'pass')
    subject, body = vy.git(wt, 'log', '-1', '--format=%s%x00%b', fix).split('\x00', 1)
    return {'id': fix[:10], 'fix': fix, 'base': base, 'subject': subject, 'body': body.strip(), 'src': c['src'],
            'tests': c['tests'], 'support': c['support'], 'f2p': f2p, 'p2p': p2p,
            'n_fix_tests': len(r1), 'uncollected_at_base': sorted(set(stable_pass) - set(rb)),
            'diffstat': vy.git(wt, 'diff', '--shortstat', base, fix, '--', *c['src'])}


def mine(prof, since, max_src, jobs, limit):
    d = TASKS(prof)
    d.mkdir(parents=True, exist_ok=True)
    cands = candidates(prof, since, max_src)[:limit]
    vy.log(f'{len(cands)} candidates')
    done = {p.stem for p in d.glob('*.json')} | {p.stem[:-5] for p in d.glob('*.skip.json')}
    todo = [c for c in cands if c['fix'][:10] not in done]
    kept = 0
    with cf.ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(mine_one, prof, c): c for c in todo}
        for fut in cf.as_completed(futs):
            c = futs[fut]
            try:
                t = fut.result()
            except Exception as e:
                vy.jdump(d / f"{c['fix'][:10]}.skip.json", {'fix': c['fix'], 'why': str(e)[-500:]})
                continue
            if t['f2p']:
                vy.jdump(d / f"{t['id']}.json", t)
                kept += 1
                vy.log(f"KEEP {t['id']} f2p={len(t['f2p'])} p2p={len(t['p2p'])} {t['subject'][:60]}")
            else:
                vy.jdump(d / f"{t['id']}.skip.json", {**t, 'why': 'no fail-to-pass test'})
    vy.log(f'kept {kept} of {len(todo)} new; total {len(list(d.glob("*[!p].json")))}')


DESC_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['title', 'description', 'interface', 'acceptance'],
               'properties': {'title': {'type': 'string'}, 'description': {'type': 'string'},
                              'interface': {'type': 'array', 'items': {'type': 'string'}},
                              'acceptance': {'type': 'array', 'items': {'type': 'string'}}}}
LEAK_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['leaks', 'why'],
               'properties': {'leaks': {'type': 'boolean'}, 'why': {'type': 'string'}}}
PICK_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['pick', 'why'],
               'properties': {'pick': {'type': 'string'}, 'why': {'type': 'string'}}}


def task_list(prof, n=None, seed=7):
    """The task set: a fixed, seeded sample (so arms and reruns see the same tasks)."""
    ts = sorted((vy.jload(p) for p in TASKS(prof).glob('*.json') if not p.name.endswith('.skip.json')), key=lambda t: t['id'])
    random.Random(seed).shuffle(ts)
    return ts[:n] if n else ts


def task_text(t):
    d = t['desc']
    iface = ('\n\nInterface the tests use: ' + '; '.join(d['interface'])) if d['interface'] else ''
    return f"{d['title']}\n\n{d['description']}{iface}"


def describe_one(prof, t, wt):
    from voyager import render
    vy.git(wt, 'checkout', '-q', '-f', t['base'])
    fix = vy.git(wt, 'diff', t['base'], t['fix'], '--', *t['src'])
    tdiff = vy.git(wt, 'diff', t['base'], t['fix'], '--', *t['tests'])[:12000]
    logd = Path(prof['home']) / 'backtest' / 'logs'
    logd.mkdir(parents=True, exist_ok=True)
    fb = ''
    for attempt in range(2):
        out = vy.claude(render('describe.md', REPO_NAME=prof['name'], WT=wt, MESSAGE=f"{t['subject']}\n{t['body']}",
                               FIX_DIFF=fix[:15000], TEST_DIFF=tdiff) + fb, wt, logd / f"describe-{t['id']}-{attempt}.json",
                        max_turns=25, timeout=900, schema=DESC_SCHEMA, allowed=['Read', 'Grep', 'Glob'])
        d = out.get('structured_output')
        if not d:
            continue
        t['desc'] = d
        chk = vy.claude(render('leak.md', TASK=json.dumps(d, indent=1), FIX_DIFF=fix[:15000]), wt,
                        logd / f"leak-{t['id']}-{attempt}.json", max_turns=3, timeout=300, schema=LEAK_SCHEMA,
                        allowed=['Read'])
        verdict = chk.get('structured_output') or {'leaks': True, 'why': 'no verdict'}
        t['leak_check'] = verdict
        t['desc_cost_usd'] = t.get('desc_cost_usd', 0) + float(out.get('total_cost_usd') or 0) + float(chk.get('total_cost_usd') or 0)
        if not verdict['leaks']:
            return t
        fb = f"\n\nA reviewer said your previous version leaked the fix: {verdict['why']}. Rewrite without it."
    t['desc_dropped'] = True
    return t


def describe(prof, n, jobs):
    ts = [t for t in task_list(prof, n) if 'desc' not in t or t.get('desc_dropped')]
    def go(t):
        wt = _wt(prof)
        t = describe_one(prof, t, wt)
        vy.jdump(TASKS(prof) / f"{t['id']}.json", t)
        vy.log(f"DESC {t['id']} {'DROPPED (leak)' if t.get('desc_dropped') else t['desc']['title'][:70]}")
    with cf.ThreadPoolExecutor(jobs) as ex:
        list(ex.map(go, ts))


def _job(prof, jid, t, mode, hidden='writer'):
    import voyager
    a = argparse.Namespace(id=jid, goal=task_text(t), title=t['desc']['title'], accept=t['desc']['acceptance'],
                           base=t['base'], mode=mode, pr='local', hidden=hidden, ui=None, mutation_floor=0.5,
                           retries=2, turns=80, minutes=30)
    jid = voyager.new_job(prof, a)
    return voyager.Job(prof, jid)


def run_arm(prof, arm, n, jobs, k=3):
    """A = oneshot, B = factory, C = best-of-k oneshots picked by the model itself (equal-spend control)."""
    import voyager
    ts = [t for t in task_list(prof, n) if 'desc' in t and not t.get('desc_dropped')]
    res_dir = Path(prof['home']) / 'backtest' / 'arms' / arm
    res_dir.mkdir(parents=True, exist_ok=True)

    def go(t):
        rf = res_dir / f"{t['id']}.json"
        if (vy.jload(rf) or {}).get('final'):
            return
        if arm in ('A', 'B'):
            j = _job(prof, f"{arm}-{t['id']}", t, 'oneshot' if arm == 'A' else 'factory')
            s = j.run()
            r = {'task': t['id'], 'cand': s.get('cand'), 'submitted': bool(s.get('cand')) and not s.get('escalated'),
                 'escalated': s.get('escalated'), 'cost_usd': s['cost_usd'], 'attempts': s['attempt'] + 1,
                 'secs': sum(x.get('secs') or 0 for x in s['sessions']), 'builder_says': s.get('builder_says')}
        else:
            subs = []
            for i in range(k):
                s = _job(prof, f"C{i}-{t['id']}", t, 'oneshot').run()
                subs.append(s)
            cands = [(f"voyager/C{i}-{t['id']}", s.get('cand')) for i, s in enumerate(subs) if s.get('cand')]
            cost = sum(s['cost_usd'] for s in subs)
            secs = sum(x.get('secs') or 0 for s in subs for x in s['sessions'])
            pick = None
            if len(cands) == 1:
                pick = cands[0]
            elif cands:
                wt = Path(prof['home']) / 'wt' / f"C-{t['id']}-select"
                vy.worktree(prof, t['base'], wt)
                listing = '\n'.join(f"- branch `{b}`: {vy.git(wt, 'log', '--format=%s', f"{t['base']}..{c}")}" for b, c in cands)
                out = vy.claude(voyager.render('selector.md', K=len(cands), WT=wt, TEST_CWD=prof['test_cwd'],
                                               TEST_CMD=voyager.test_cmd(prof), GOAL=task_text(t), CANDIDATES=listing),
                                wt, Path(prof['home']) / 'backtest' / 'logs' / f"select-{t['id']}.json", max_turns=40,
                                timeout=1200, schema=PICK_SCHEMA, disallowed=vy.WORKER_DENY, env_extra=prof.get('test_env', {}))
                cost += float(out.get('total_cost_usd') or 0)
                secs += out.get('secs') or 0
                want = (out.get('structured_output') or {}).get('pick', '')
                pick = next((x for x in cands if x[0] in want or want in x[0]), cands[0])
                vy.drop_worktree(prof, wt)
            r = {'task': t['id'], 'cand': pick[1] if pick else None, 'picked': pick[0] if pick else None,
                 'submitted': bool(pick), 'cost_usd': round(cost, 4), 'secs': secs, 'k': k, 'n_cands': len(cands)}
        r['final'] = True
        vy.jdump(rf, r)
        vy.log(f"{arm} {t['id']} submitted={r['submitted']} ${r['cost_usd']:.2f}")

    with cf.ThreadPoolExecutor(jobs) as ex:
        for f in [ex.submit(go, t) for t in ts]:
            try:
                f.result()
            except vy.InfraError as e:
                vy.log(f'{arm} task PAUSED (infra, not scored): {e}'[:300])
            except Exception as e:
                vy.log(f'{arm} task error: {e!r}'[:400])


def judge(prof, n):
    """The hidden judge: the human fix's tests (fail-to-pass + pass-to-pass) on each submitted candidate. Also the
    judge's own strength: how many mutants of the human fix those tests kill."""
    import mutate
    wt = _wt(prof)
    arms = sorted(p.name for p in (Path(prof['home']) / 'backtest' / 'arms').glob('*'))
    for t in task_list(prof, n):
        if 'desc' not in t or t.get('desc_dropped'):
            continue
        if 'judge_strength' not in t:
            t['judge_strength'] = mutate.score(prof, wt, t['base'], t['fix'], t['src'], t['tests'], max_mutants=10)
            vy.jdump(TASKS(prof) / f"{t['id']}.json", t)
        for arm in arms:
            rf = Path(prof['home']) / 'backtest' / 'arms' / arm / f"{t['id']}.json"
            r = vy.jload(rf)
            if not r or 'judge' in r:
                continue
            if not r.get('cand'):
                r['judge'] = {'pass': False, 'why': 'no candidate'}
            else:
                at(wt, r['cand'])
                vy.git(wt, 'checkout', '-q', t['fix'], '--', *t['support'])
                res, _, tail = vy.pytest(prof, wt, t['tests'])
                bad = sorted(x for x in t['f2p'] + t['p2p'] if res.get(x) != 'pass')
                r['judge'] = {'pass': not bad, 'failed': bad, 'f2p_failed': sorted(set(bad) & set(t['f2p']))}
            vy.jdump(rf, r)
            vy.log(f"JUDGE {arm} {t['id']} {'pass' if r['judge']['pass'] else 'FAIL'}")


def mcnemar(b, c):
    """Exact two-sided McNemar p-value from the discordant counts b, c."""
    from math import comb
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def wilson(k, n, z=1.645):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (round((c - r) / d, 3), round((c + r) / d, 3))


W = {'input_tokens': 1.0, 'cache_creation_input_tokens': 1.25, 'cache_read_input_tokens': 0.1, 'output_tokens': 5.0}


def _weighted_tokens(sid):
    """Price-weighted tokens of one session from its Claude Code transcript (standard Anthropic price ratios)."""
    hits = list((Path.home() / '.claude' / 'projects').glob(f'*/{sid}.jsonl'))
    if not hits:
        return None
    seen, w = set(), 0.0
    for line in hits[0].read_text(errors='replace').splitlines():
        try:
            m = json.loads(line).get('message') or {}
        except json.JSONDecodeError:
            continue
        u, mid = m.get('usage'), m.get('id')
        if u and mid not in seen:
            seen.add(mid)
            w += sum(u.get(k, 0) * f for k, f in W.items())
    return w


def recover_costs(prof):
    """Sessions killed by our timeout report no cost. Estimate them: $/weighted-token calibrated on the sessions
    that did report a cost, applied to the timed-out ones. Written as cost_usd_est (raw cost_usd kept)."""
    H = Path(prof['home'])
    rows, timed = [], []
    for st in (H / 'jobs').glob('*/state.json'):
        s = vy.jload(st)
        for x in s.get('sessions', []):
            w = _weighted_tokens(x['id'])
            if not w:
                continue
            (rows if x.get('cost_usd') else timed).append((st.parent.name, x, w))
    rate = sorted(x['cost_usd'] / w for _, x, w in rows)[len(rows) // 2]   # median $ per weighted token
    extra = {}
    for job, x, w in timed:
        extra[job] = extra.get(job, 0) + w * rate
    for arm_dir in (H / 'backtest' / 'arms').glob('*'):
        for f in arm_dir.glob('*.json'):
            r = vy.jload(f)
            jobs = [f"{arm_dir.name}-{r['task']}"] if arm_dir.name in 'AB' else [f"C{i}-{r['task']}" for i in range(r.get('k', 3))]
            r['cost_usd_est'] = round(r['cost_usd'] + sum(extra.get(j, 0) for j in jobs), 4)
            vy.jdump(f, r)
    return {'rate_usd_per_weighted_token': rate, 'calibration_sessions': len(rows), 'timed_out_sessions': len(timed),
            'estimated_usd_added': round(sum(extra.values()), 2)}


def score(prof):
    base = Path(prof['home']) / 'backtest' / 'arms'
    R = {arm.name: {r['task']: r for r in (vy.jload(p) for p in arm.glob('*.json')) if r and 'judge' in r}
         for arm in base.glob('*')}
    common = sorted(set.intersection(*(set(v) for v in R.values()))) if R else []
    rows = {}
    for arm, rs in sorted(R.items()):
        sub = [rs[t] for t in common if rs[t]['submitted']]
        ok = [r for r in sub if r['judge']['pass']]
        solved = sum(rs[t]['judge']['pass'] and rs[t]['submitted'] for t in common)
        rows[arm] = {'n': len(common), 'submitted': len(sub), 'correct_of_submitted': len(ok),
                     'precision': round(len(ok) / len(sub), 3) if sub else None, 'precision_90ci': wilson(len(ok), len(sub)),
                     'false_accepts': len(sub) - len(ok), 'solve_rate': round(solved / len(common), 3) if common else None,
                     'escalated': sum(1 for t in common if rs[t].get('escalated')),
                     'cost_usd_total': round(sum(rs[t].get('cost_usd_est', rs[t]['cost_usd']) for t in common), 2),
                     'cost_per_accepted_correct': round(sum(rs[t].get('cost_usd_est', rs[t]['cost_usd']) for t in common) / max(1, len(ok)), 2),
                     'cost_note': 'estimated: includes timed-out sessions priced from their transcripts',
                     'minutes_mean': round(sum(rs[t].get('secs') or 0 for t in common) / 60 / max(1, len(common)), 1)}
    pairs = {}
    names = sorted(R)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            # paired on "shipped a wrong answer" (submitted and failed the judge): the cost a human pays to find it
            fa = lambda arm, t: R[arm][t]['submitted'] and not R[arm][t]['judge']['pass']
            n10 = sum(fa(a, t) and not fa(b, t) for t in common)
            n01 = sum(fa(b, t) and not fa(a, t) for t in common)
            s10 = sum(R[a][t]['judge']['pass'] and R[a][t]['submitted'] and not (R[b][t]['judge']['pass'] and R[b][t]['submitted']) for t in common)
            s01 = sum(R[b][t]['judge']['pass'] and R[b][t]['submitted'] and not (R[a][t]['judge']['pass'] and R[a][t]['submitted']) for t in common)
            pairs[f'{a} vs {b}'] = {'false_accept_only_' + a: n10, 'false_accept_only_' + b: n01, 'p_false_accept': round(mcnemar(n10, n01), 4),
                                   'solved_only_' + a: s10, 'solved_only_' + b: s01, 'p_solve': round(mcnemar(s10, s01), 4)}
    return {'tasks': common, 'arms': rows, 'paired': pairs}


def packet(prof, n_sample=30, seed=11):
    """Blind review packet for a human: submitted changes from every arm, shuffled, arm hidden. The key is a
    separate file; read it only after the verdicts are written."""
    base = Path(prof['home']) / 'backtest'
    items = []
    for arm in sorted(p.name for p in (base / 'arms').glob('*')):
        for r in (vy.jload(f) for f in (base / 'arms' / arm).glob('*.json')):
            if r and r.get('submitted') and r.get('cand'):
                items.append((arm, r))
    random.Random(seed).shuffle(items)
    items = items[:n_sample]
    wt = _wt(prof)
    md, key = ['# Blind review: would you merge this?', '',
               'For each change: read the task, read the diff, write `merge` / `no` and one reason under it.', ''], {}
    for i, (arm, r) in enumerate(items, 1):
        t = vy.jload(TASKS(prof) / f"{r['task']}.json")
        rid = f'R{i:02d}'
        key[rid] = {'arm': arm, 'task': r['task'], 'judge': r.get('judge', {}).get('pass')}
        diff = vy.git(wt, 'diff', t['base'], r['cand'])
        md += [f'## {rid}', '', '**Task:** ' + task_text(t).replace('\n', '\n> '), '', '```diff', diff[:20000], '```', '',
               '**Verdict:** ', '']
    (base / 'review-packet.md').write_text('\n'.join(md))
    vy.jdump(base / 'review-key.json', key)
    return str(base / 'review-packet.md')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd')
    ap.add_argument('--profile', default='exampleapp')
    ap.add_argument('--since', default='18 months ago')
    ap.add_argument('--max-src', type=int, default=5)
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--limit', type=int, default=10_000)
    ap.add_argument('--n', type=int, default=40)
    ap.add_argument('--arm', default='A')
    ap.add_argument('--k', type=int, default=3)
    a = ap.parse_args()
    prof = vy.profile(a.profile)
    if a.cmd == 'candidates':
        print(json.dumps([c['fix'] for c in candidates(prof, a.since, a.max_src)]))
    elif a.cmd == 'mine':
        mine(prof, a.since, a.max_src, a.jobs, a.limit)
    elif a.cmd == 'describe':
        describe(prof, a.n, a.jobs)
    elif a.cmd == 'run':
        run_arm(prof, a.arm, a.n, a.jobs, a.k)
    elif a.cmd == 'judge':
        judge(prof, a.n)
    elif a.cmd == 'score':
        print(json.dumps(score(prof), indent=1))
    elif a.cmd == 'costs':
        print(json.dumps(recover_costs(prof), indent=1))
    elif a.cmd == 'packet':
        print(packet(prof))
    elif a.cmd == 'list':
        for t in task_list(prof, a.n):
            print(t['id'], len(t['f2p']), 'desc' in t, t['subject'][:80])


if __name__ == '__main__':
    main()
