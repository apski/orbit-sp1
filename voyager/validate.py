"""The protected validator: judges a candidate commit against its base with rules the builder cannot change.

Everything it reads comes from the BASE commit's profile and from outside the worktree (hidden tests), never from the
candidate. Checks, in order (each one is a row in the evidence):

  scope        files touched; protected paths (judge machinery, pinned corpora) untouched
  weakening    no existing test deleted, no assert line removed from an existing test file
  f2p          the candidate's own new/changed tests: fail on base code, pass on the candidate (>= 1 required)
  flake        those tests pass 3 times in a row on the candidate
  regression   every test that passed on base in the touched test files still passes
  gates        always-run suites (profile `gates`, or the job's): what passed on base still passes
  hidden       holdout tests (kept outside the worktree) pass on the candidate
  mutation     targeted mutants of the candidate's changed lines: the share its tests kill (>= floor)
"""
import re
from pathlib import Path
import vy, mutate


def changed(wt, base, cand):
    out = vy.git(wt, 'diff', '--name-status', '--no-renames', base, cand)
    return [(l.split('\t')[0][0], l.split('\t')[-1]) for l in out.splitlines() if l.strip()]


def removed_asserts(wt, base, cand, path):
    d = vy.git(wt, 'diff', '-U0', base, cand, '--', path)
    return [l[1:].strip() for l in d.splitlines() if l.startswith('-') and not l.startswith('---')
            and re.search(r'\bassert|\.assert\w+\(|pytest\.raises', l)]


def overlay_run(prof, wt, base, cand, tests, files_from_cand):
    """Run `tests` on base code with the candidate's test-side files laid over it; restore the candidate after."""
    vy.git(wt, 'checkout', '-q', '-f', base)
    if files_from_cand:
        vy.git(wt, 'checkout', '-q', cand, '--', *files_from_cand)
    res, _, tail = vy.pytest(prof, wt, tests)
    vy.git(wt, 'checkout', '-q', '-f', cand)
    vy.git(wt, 'clean', '-q', '-fd')
    return res, tail


def validate(prof, wt, base, cand, hidden=None, mutation_floor=0.5, max_mutants=12, f2p_required=True, gates=None,
             gate_timeout=None):
    """wt: a worktree we may check out freely (not the builder's). hidden: {'files': {relpath: text}, 'run': [test files], 'tests': [ids that must pass]}
    Returns evidence dict with verdict pass|fail and per-check rows."""
    rows, ok = [], True

    def row(name, passed, detail):
        nonlocal ok
        ok &= bool(passed)
        rows.append({'check': name, 'pass': bool(passed), 'detail': detail})

    vy.git(wt, 'checkout', '-q', '-f', cand)
    files = changed(wt, base, cand)
    src = [f for s, f in files if s != 'D' and f.startswith(prof['test_cwd'] + '/') and any(f.endswith(e) for e in prof['source_ext']) and not vy.is_test(prof, f) and '/tests/' not in f]
    tests = [f for s, f in files if s != 'D' and vy.is_test(prof, f)]
    side = [f for s, f in files if s != 'D' and f not in src]  # tests + fixtures the candidate brought
    prot = [f for s, f in files if any(f.startswith(p) or f == p for p in prof['protected'])]
    row('scope', not prot and bool(src or tests or (files and not f2p_required)), {'src': src, 'tests': tests, 'protected_touched': prot,
                                                'n_files': len(files)})

    deleted_tests = [f for s, f in files if s == 'D' and vy.is_test(prof, f)]
    existing = set(vy.git(wt, 'ls-tree', '-r', '--name-only', base).splitlines())
    weak = {f: removed_asserts(wt, base, cand, f) for f in tests if f in existing}
    weak = {f: v for f, v in weak.items() if v}
    row('weakening', not deleted_tests and not weak, {'deleted_tests': deleted_tests, 'removed_asserts': weak})

    # candidate's tests on the candidate (3x for flake), then on base code
    runs = [vy.pytest(prof, wt, tests)[0] for _ in range(3)] if tests else [{}]
    stable = {t for t in runs[0] if all(r.get(t) == 'pass' for r in runs)}
    flaky = sorted(t for t in runs[0] if runs[0][t] == 'pass' and t not in stable)
    on_base, _ = overlay_run(prof, wt, base, cand, tests, side) if tests and src else ({}, '')
    f2p = sorted(t for t in stable if on_base.get(t) in ('fail', 'error'))
    if f2p_required:
        row('f2p', len(f2p) >= 1, {'f2p': f2p, 'n_cand_tests': len(runs[0]),
                                   'failing_on_cand': sorted(t for t, v in runs[0].items() if v in ('fail', 'error'))})
    row('flake', not flaky, {'flaky': flaky})

    # regression: what passed on base (base's version of the touched test files) still passes on candidate
    old_tests = [f for f in tests if f in existing]
    if old_tests:
        vy.git(wt, 'checkout', '-q', '-f', base)
        before, _, _ = vy.pytest(prof, wt, old_tests)
        vy.git(wt, 'checkout', '-q', '-f', cand)
        broke = sorted(t for t, v in before.items() if v == 'pass' and runs[0].get(t) not in ('pass', None))
        row('regression', not broke, {'broke': broke, 'n_base_pass': sum(v == 'pass' for v in before.values())})

    gates = prof.get('gates', []) if gates is None else gates
    if gates:
        # always-run suites (e.g. goldens): whatever passes on base must pass on the candidate. Base results are
        # cached per base sha: every attempt of every job on the same base reuses one run.
        cache = Path(prof['home']) / 'gates' / f'{base}.json'
        before = vy.jload(cache) or None   # an empty cached result is a gate that never ran: re-run it
        tmo = gate_timeout or prof.get('gate_timeout', 1800)
        if before is None:
            vy.git(wt, 'checkout', '-q', '-f', base)
            before, rc_b, _ = vy.pytest(prof, wt, gates, timeout=tmo)
            if before:
                vy.jdump(cache, before)
            vy.git(wt, 'checkout', '-q', '-f', cand)
        after, rc_a, _ = vy.pytest(prof, wt, gates, timeout=tmo)
        if not before or not after:
            # the gate produced no results (timeout under load, a crash at collection): that is the harness, not
            # the candidate. Never send a builder to fix it; the job pauses and resumes like an API outage.
            raise vy.InfraError(f'gate suite produced no results ({"base" if not before else "candidate"} run, '
                                f'timeout {tmo}s): rerun when the machine is less loaded, or raise gate_timeout')
        broke = sorted(t for t, v in before.items() if v == 'pass' and after.get(t) != 'pass')
        row('gates', not broke, {'suites': gates, 'broke': broke[:20], 'n_broke': len(broke),
                                 'n_base_pass': sum(v == 'pass' for v in before.values())})

    if hidden:
        for rel, text in hidden['files'].items():
            p = Path(wt) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        hres, _, tail = vy.pytest(prof, wt, hidden['run'])
        want = hidden['tests']
        got = {t: hres.get(t, 'missing') for t in want}
        bad = sorted(t for t, v in got.items() if v != 'pass')
        row('hidden', not bad, {'failed': bad, 'n': len(want), 'tail': tail[-800:] if bad else ''})
        vy.git(wt, 'checkout', '-q', '-f', cand)
        vy.git(wt, 'clean', '-q', '-fd')

    if src and tests and mutation_floor:
        m = mutate.score(prof, wt, base, cand, src, tests, max_mutants=max_mutants)
        if m['n'] == 0:
            m['note'] = 'no mutation sites on the changed lines: this check says nothing about these tests'
        row('mutation', m['n'] == 0 or m['killed'] / m['n'] >= mutation_floor, m)

    return {'verdict': 'pass' if ok else 'fail', 'base': base, 'candidate': cand, 'checks': rows}


def feedback(ev):
    """What a builder is told when validation fails: which check and the facts, never the hidden tests' code."""
    lines = []
    for r in ev['checks']:
        if r['pass']:
            continue
        d = dict(r['detail'])
        if r['check'] == 'hidden':
            d = {'failed_count': len(d['failed']), 'note': 'held-out scenarios failed; they test the behaviour the task describes'}
        if r['check'] == 'mutation':
            d = {'killed': d['killed'], 'n': d['n'], 'survivors': d['survivors'][:6],
                 'note': 'your tests do not catch these deliberate faults in your change: add assertions that would'}
        lines.append(f"- {r['check']}: {d}")
    return '\n'.join(lines)
