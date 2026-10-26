"""Constraints, legible: what the tests and rules pin, readable in seconds by an agent (JSON) or a human (markdown).

  map    <wt> <path...>            per test file: each test as a scenario (name -> words, docstring), assertion count,
                                   the code it exercises (imports), plus the unit's mutation score if strengthen ran
  delta  <wt> <base> <cand>        what a change does to the constraints: tests added / removed / weakened
                                   (fewer assertions) / strengthened, and protected rules touched

Python test files via stdlib ast; other runners plug in at `parse_tests`.
"""
import ast, json, re
from pathlib import Path
import vy

ASSERT = re.compile(r'^assert|\.assert\w*$|^raises$|^approx$')


def _asserts(fn):
    n = 0
    for x in ast.walk(fn):
        if isinstance(x, ast.Assert):
            n += 1
        elif isinstance(x, ast.Call):
            name = x.func.attr if isinstance(x.func, ast.Attribute) else getattr(x.func, 'id', '')
            n += bool(ASSERT.search(name or ''))
        elif isinstance(x, ast.With):
            n += any('raises' in ast.unparse(i.context_expr) for i in x.items)
    return n


def words(name):
    return re.sub(r'^test_?', '', name).replace('_', ' ').strip()


def parse_tests(text):
    """{test_name: {'scenario', 'doc', 'asserts', 'line'}} and the modules the file imports."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, []
    tests, imports = {}, set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module)
        elif isinstance(n, ast.Import):
            imports.update(a.name for a in n.names)
    def visit(body, prefix=''):
        for n in body:
            if isinstance(n, ast.ClassDef) and n.name.startswith('Test'):
                visit(n.body, n.name + '::')
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test'):
                doc = (ast.get_docstring(n) or '').strip().split('\n')[0]
                tests[prefix + n.name] = {'scenario': words(n.name), 'doc': doc, 'asserts': _asserts(n), 'line': n.lineno}
    visit(tree.body)
    return tests, sorted(i for i in imports if not i.startswith(('pytest', 'unittest', 'pandas', 'numpy')))


def cmap(prof, wt, paths, scores=None):
    out = []
    for rel in paths:
        p = Path(wt) / rel
        files = sorted(p.rglob('*.py')) if p.is_dir() else [p]
        for f in files:
            r = str(f.relative_to(wt))
            if not vy.is_test(prof, r):
                continue
            tests, imports = parse_tests(f.read_text(errors='replace'))
            out.append({'file': r, 'exercises': imports, 'tests': tests,
                        'unnamed': sorted(t for t, v in tests.items() if not v['doc']),
                        'mutation': (scores or {}).get(r)})
    return out


def delta(prof, wt, base, cand):
    """Constraint changes between two commits (test files only), from git blobs: no checkout needed."""
    names = vy.git(wt, 'diff', '--name-status', '--no-renames', base, cand).splitlines()
    res = {'added': [], 'removed': [], 'weakened': [], 'strengthened': [], 'protected_touched': []}
    for line in names:
        st, f = line.split('\t')[0][0], line.split('\t')[-1]
        if any(f.startswith(p) for p in prof.get('protected', [])):
            res['protected_touched'].append(f)
        if not vy.is_test(prof, f) or not f.endswith('.py'):
            continue
        old = parse_tests(vy.git(wt, 'show', f'{base}:{f}', check=False) if st != 'A' else '')[0]
        new = parse_tests(vy.git(wt, 'show', f'{cand}:{f}', check=False) if st != 'D' else '')[0]
        for t in sorted(set(new) - set(old)):
            res['added'].append(f'{f}::{t} — {new[t]["doc"] or new[t]["scenario"]} ({new[t]["asserts"]} asserts)')
        for t in sorted(set(old) - set(new)):
            res['removed'].append(f'{f}::{t} — {old[t]["scenario"]}')
        for t in sorted(set(old) & set(new)):
            a, b = old[t]['asserts'], new[t]['asserts']
            if b < a:
                res['weakened'].append(f'{f}::{t} asserts {a} -> {b}')
            elif b > a:
                res['strengthened'].append(f'{f}::{t} asserts {a} -> {b}')
    return res


def delta_md(d):
    lines = []
    for k in ('added', 'strengthened', 'weakened', 'removed', 'protected_touched'):
        if d[k]:
            lines.append(f'**{k}** ({len(d[k])})')
            lines += [f'- {x}' for x in d[k]]
    return '\n'.join(lines) or '(no test or rule changes)'


def map_md(m):
    out = []
    for f in m:
        ms = f' · mutation {f["mutation"]}' if f['mutation'] else ''
        out.append(f'### {f["file"]}\nexercises: {", ".join(f["exercises"][:8]) or "?"}{ms}\n')
        for t, v in f['tests'].items():
            out.append(f'- **{v["scenario"]}** ({v["asserts"]}a, L{v["line"]}){" — " + v["doc"] if v["doc"] else ""}')
        out.append('')
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    prof = vy.profile(sys.argv[2] if len(sys.argv) > 2 and not Path(sys.argv[2]).exists() else 'python-service')
    print(json.dumps(cmap(prof, '.', sys.argv[1:]), indent=1))
