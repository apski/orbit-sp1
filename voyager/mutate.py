"""Targeted mutants: small deliberate faults in the lines a change touched (Meta ACH / SWE-ABS idea, stdlib ast).

A mutant the tests don't catch ("survivor") means the tests can't tell the change from a plausibly wrong one.
Used by the validator (score a candidate's tests) and the strengthening loop (write tests that kill survivors).

  python3 mutate.py <file.py> <first_line> <last_line>   list the mutants for a line range (debugging)
"""
import ast, copy, sys
from pathlib import Path
import vy

CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
       ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}
BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult, ast.Mod: ast.FloorDiv}


def _sites(tree, lines):
    """(node, kind) pairs on the given lines, in source order."""
    out = []
    for n in ast.walk(tree):
        ln = getattr(n, 'lineno', None)
        if ln not in lines:
            continue
        if isinstance(n, ast.Compare) and type(n.ops[0]) in CMP:
            out.append((n, 'cmp'))
        elif isinstance(n, ast.BinOp) and type(n.op) in BIN:
            out.append((n, 'bin'))
        elif isinstance(n, ast.BoolOp):
            out.append((n, 'bool'))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            out.append((n, 'not'))
        elif isinstance(n, ast.Constant) and type(n.value) in (int, float, bool):
            out.append((n, 'const'))
        elif isinstance(n, ast.Return) and n.value is not None:
            out.append((n, 'return'))
        elif isinstance(n, (ast.If, ast.While)):
            out.append((n, 'cond'))
        elif isinstance(n, ast.Call) and len(n.args) == 1 and not n.keywords and not isinstance(n.args[0], ast.Starred):
            out.append((n, 'unwrap'))    # str(x) -> x, round(x) -> x, set(xs) -> xs
        elif isinstance(n, ast.Assign) and not isinstance(n.value, ast.Constant):
            out.append((n, 'assign'))
    return sorted(out, key=lambda x: (x[0].lineno, x[0].col_offset, x[1]))


def _apply(n, kind):
    if kind == 'cmp':
        n.ops[0] = CMP[type(n.ops[0])]()
        return 'compare flipped'
    if kind == 'bin':
        n.op = BIN[type(n.op)]()
        return 'arithmetic swapped'
    if kind == 'bool':
        n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
        return 'and/or swapped'
    if kind in ('not', 'unwrap'):
        return kind  # handled by the caller (replace the node with its operand / argument)
    if kind == 'assign':
        n.value = ast.Constant(None)
        return 'assigns None'
    if kind == 'const':
        v = n.value
        n.value = (not v) if isinstance(v, bool) else (v + 1 if v == 0 else 0) if isinstance(v, int) else v * 2 + 1
        return f'constant {v!r} -> {n.value!r}'
    if kind == 'return':
        n.value = ast.Constant(None)
        return 'returns None'
    if kind == 'cond':
        n.test = ast.UnaryOp(ast.Not(), n.test)
        return 'condition negated'


def mutants(source, lines):
    """Yield (line, description, mutated_source) for each mutation site on `lines`."""
    tree = ast.parse(source)
    for i, (node, kind) in enumerate(_sites(tree, set(lines))):
        t2 = copy.deepcopy(tree)
        n2, k2 = _sites(t2, set(lines))[i]
        if kind in ('not', 'unwrap'):
            # replace `not x` with `x`, `f(x)` with `x`, in its parent
            repl = n2.operand if kind == 'not' else n2.args[0]
            for parent in ast.walk(t2):
                for f, v in ast.iter_fields(parent):
                    if v is n2:
                        setattr(parent, f, repl)
                    elif isinstance(v, list) and any(x is n2 for x in v):
                        setattr(parent, f, [repl if x is n2 else x for x in v])
            desc = 'not removed' if kind == 'not' else f'{ast.unparse(n2.func)}(...) unwrapped'
        else:
            desc = _apply(n2, kind)
        try:
            yield node.lineno, desc, ast.unparse(ast.fix_missing_locations(t2))
        except Exception:
            continue


def changed_lines(wt, base, cand, path):
    d = vy.git(wt, 'diff', '-U0', base, cand, '--', path)
    lines = set()
    for l in d.splitlines():
        if l.startswith('@@'):
            plus = l.split('+')[1].split(' ')[0]
            start, _, n = plus.partition(',')
            n = int(n) if n else 1
            lines.update(range(int(start), int(start) + n))
    return lines


def pick(items, k):
    """k items spread evenly over the list (deterministic)."""
    if len(items) <= k:
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def score(prof, wt, base, cand, src, tests, max_mutants=12, lines_for=None):
    """Mutate the candidate's changed lines in `src`, run `tests` per mutant. Worktree is left at cand, clean."""
    vy.git(wt, 'checkout', '-q', '-f', cand)
    baseline, _, _ = vy.pytest(prof, wt, tests)
    green = {t for t, v in baseline.items() if v == 'pass'}
    if not green:
        # nothing passes on clean code, so no mutant can be told apart: unscorable, not "0 killed"
        return {'n': 0, 'killed': 0, 'pool': 0, 'survivors': [], 'unscorable': f'no test passes on clean code ({len(baseline)} ran)'}
    pool = []
    for f in src:
        p = Path(wt) / f
        if not p.exists():
            continue
        text = p.read_text()
        lines = lines_for(f) if lines_for else changed_lines(wt, base, cand, f)
        pool += [(f, ln, desc, m) for ln, desc, m in mutants(text, lines)]
    chosen = pick(pool, max_mutants)
    killed, survivors = 0, []
    for f, ln, desc, m in chosen:
        p = Path(wt) / f
        orig = p.read_text()
        p.write_text(m)
        res, rc, _ = vy.pytest(prof, wt, tests, timeout=300)
        p.write_text(orig)
        dead = rc == 124 or any(res.get(t) != 'pass' for t in green) or not res
        killed += dead
        if not dead:
            survivors.append(f'{f}:{ln} {desc}')
    vy.git(wt, 'checkout', '-q', '-f', cand)
    # ponytail: no equivalent-mutant filter; a few survivors may be harmless rewrites. Floor < 1.0 absorbs them.
    return {'n': len(chosen), 'killed': killed, 'pool': len(pool), 'survivors': survivors}


if __name__ == '__main__':
    src = Path(sys.argv[1]).read_text()
    for ln, desc, _ in mutants(src, range(int(sys.argv[2]), int(sys.argv[3]) + 1)):
        print(ln, desc)
