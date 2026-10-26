"""The strengthening loop: raise what a unit's tests can tell apart, as its own job (never inside a builder's).

  voyager strengthen <src file> [<test,files>]

1. score: targeted mutants over the unit's code; survivors = behaviour no test pins.
2. a separate session writes characterization tests aimed at the survivors (prompts/strengthen.md).
3. accept a new test only if it passes 3x on clean code (no flake); drop the rest.
4. re-score with the new tests: the unit's floor (killed / mutants) is recorded in ledger.jsonl and must not fall.
5. the accepted file is committed on branch voyager/strengthen-<unit> for review as a normal PR.
"""
import ast, json, re, time
from pathlib import Path
import vy, mutate, constraints as C


def tests_for(prof, wt, src):
    """Test files that import the unit (by dotted module, or `from <package> import <unit>`)."""
    rel = src[len(prof['test_cwd']) + 1:] if src.startswith(prof['test_cwd'] + '/') else src
    mod = rel[:-3].replace('/', '.')
    pkg, _, leaf = mod.rpartition('.')
    pat = re.compile(rf'(from\s+{re.escape(mod)}\s+import|import\s+{re.escape(mod)}\b|from\s+{re.escape(pkg)}\s+import\s+[^\n]*\b{re.escape(leaf)}\b)')
    out = []
    for f in vy.git(wt, 'ls-files', '--', prof['test_cwd']).splitlines():
        if vy.is_test(prof, f) and pat.search((Path(wt) / f).read_text(errors='replace')):
            out.append(f)
    return sorted(out)[:8]


def all_lines(text):
    """Lines inside function bodies (where behaviour lives), not imports or module constants."""
    lines = set()
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.update(range(n.body[0].lineno, (n.end_lineno or n.lineno) + 1))
    return lines


def run(prof, src, tests=None, max_mutants=20, sha='HEAD'):
    from voyager import render, test_cmd
    unit = Path(src).stem
    wt = Path(prof['home']) / 'wt' / f'strengthen-{unit}'
    base = vy.git(vy.bare(prof), 'rev-parse', sha)
    vy.worktree(prof, base, wt, branch=f'voyager/strengthen-{unit}')
    vy.git(wt, 'checkout', '-q', '-f', f'voyager/strengthen-{unit}')
    tests = tests or tests_for(prof, wt, src)
    text = (wt / src).read_text()
    lines = all_lines(text)
    before = mutate.score(prof, wt, base, base, [src], tests, max_mutants=max_mutants, lines_for=lambda f: lines)
    out_rel = f"{prof['test_cwd']}/tests/test_strengthen_{unit}.py"
    rec = {'t': time.strftime('%FT%T'), 'unit': src, 'tests': tests, 'before': before}
    if before.get('unscorable'):
        rec['skipped'] = before['unscorable']
    elif before['survivors']:
        sess = vy.claude(render('strengthen.md', REPO_NAME=prof['name'], SRC=src, WT=wt, TESTS=', '.join(tests) or '(none)',
                                SURVIVORS='\n'.join(f'- `{s}`' for s in before['survivors']), OUT=out_rel,
                                TEST_CWD=prof['test_cwd'], TEST_CMD=test_cmd(prof)),
                         wt, Path(prof['home']) / 'logs' / f'strengthen-{unit}.json', max_turns=60, timeout=1800,
                         disallowed=vy.WORKER_DENY, env_extra=prof.get('test_env', {}))
        rec['cost_usd'] = sess.get('total_cost_usd')
        if (wt / out_rel).exists():
            runs = [vy.pytest(prof, wt, [out_rel])[0] for _ in range(3)]
            good = sorted(t for t in runs[0] if all(r.get(t) == 'pass' for r in runs))
            rec['written'], rec['accepted'] = len(runs[0]), len(good)
            if good and len(good) < len(runs[0]):
                rec['dropped'] = sorted(set(runs[0]) - set(good))
                # ponytail: a failing/flaky test drops the whole file unless the agent fixes it; per-test pruning later
                (wt / out_rel).unlink()
                good = []
            if good:
                after = mutate.score(prof, wt, base, base, [src], tests + [out_rel], max_mutants=max_mutants,
                                     lines_for=lambda f: lines)
                rec['after'] = after
                rec['map'] = C.cmap(prof, wt, [out_rel])
                if after['killed'] > before['killed'] and not after.get('unscorable'):
                    vy.git(wt, 'add', '--', out_rel)
                    vy.git(wt, 'commit', '-qm', f"test({unit}): pin {after['killed'] - before['killed']} behaviours "
                           f"mutants showed unpinned ({before['killed']}/{before['n']} -> {after['killed']}/{after['n']})",
                           '--', out_rel)
                    rec['commit'] = vy.git(wt, 'rev-parse', 'HEAD')
    with open(Path(prof['home']) / 'ledger.jsonl', 'a') as f:
        f.write(json.dumps({k: v for k, v in rec.items() if k != 'map'}) + '\n')
    return rec
