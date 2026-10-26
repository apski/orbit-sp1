#!/usr/bin/env python3
"""gate: the agent gate for a React Native repo. HARNESS.md rules 3-5 (oracle, must-pass list, ratchet), enforced.

  gate install <repo>                 .orbit/gate launcher + .orbit/CLIMB.md (never overwritten)
  gate status <repo>                  one row: not installed | installed | online
  gate propose [--repo R]             the orbit.toml checks + Claude Code Stop hook for this repo, to review and paste
  gate ratchet [--repo R]             fail if any ruler got looser than at the base commit (see BASE below)
  gate fitness [--repo R]             NavLab fitness functions (new violations only)
  gate contracts [--init] [--repo R]  today's contracts vs the committed baseline (.navlab/contracts.baseline.json)
  gate program [--repo R]             NavLab program: every surface still holds its stage (program.py verify)
  gate flows [--repeat 3] [--udid U]  simwalk flows/*.mjs: must-pass flows pass N times; xfail flows still fail
  gate stop [--repo R]                Claude Code Stop hook: `verify gate` + fresh `verify gate-device` runs, or block
  gate selftest

The must-pass list is Orbit's: `[check_sets] gate` (fast, every Stop) and `gate-device` (simulator; its runs must be
newer than the app files they cover). The oracle is the checks themselves; none calls a model.

BASE: what "looser" is measured against. $GATE_BASE, else merge-base(HEAD, upstream), else HEAD. A person loosens a
ruler by committing and pushing it; an agent's local commits never move the base (it doesn't push).
ponytail: with no upstream the base is HEAD, so a local commit resets it; run long agents on a branch with an upstream.

Flows: a flow file with `export const xfail = '<repro>'` is a demonstrated bug (expected to fail). It is never
deleted or folded into a must-pass flow; when it starts passing, the gate says so and a person drops the marker.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, tomllib
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent  # the orbit-SP1 checkout
NAVLAB = HOME / 'navlab'
BASELINE = '.navlab/contracts.baseline.json'
HOOK = '"$CLAUDE_PROJECT_DIR"/.orbit/gate stop'
XFAIL = re.compile(r'^export\s+const\s+xfail\s*=', re.M)

LAUNCHER = '''#!/bin/sh
# orbit-SP1 gate for this repo (gate/gate.py). Finds the orbit-SP1 checkout at run time.
op=""
for d in "$ORBIT_HOME" "$HOME/code/orbit-sp1"; do
  [ -n "$d" ] && [ -f "$d/ORBIT-SP1.md" ] && { op="$d"; break; }
done
[ -n "$op" ] || { echo "orbit-SP1 checkout not found: clone it to ~/code/orbit-sp1 or set ORBIT_HOME" >&2; exit 127; }
here="$(cd "$(dirname "$0")/.." && pwd)"
for py in python3.15 python3.14 python3.13 python3; do command -v "$py" >/dev/null 2>&1 && break; done
exec "$py" "$op/gate/gate.py" "$@" --repo "$here"
'''


def git(repo, *a):
    p = subprocess.run(['git', '-C', str(repo), *a], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def base(repo):
    if os.environ.get('GATE_BASE'):
        return os.environ['GATE_BASE']
    for up in ('@{upstream}', 'origin/HEAD'):
        if (b := git(repo, 'merge-base', 'HEAD', up)):
            return b
    return 'HEAD'


def at(repo, ref, path):
    """A file's text at `ref` (None if it didn't exist there)."""
    p = subprocess.run(['git', '-C', str(repo), 'show', f'{ref}:{path}'], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def now(repo, path):
    f = Path(repo) / path
    return f.read_text() if f.exists() else None


def toml(text):
    try:
        return tomllib.loads(text) if text else {}
    except tomllib.TOMLDecodeError:
        return {}


def must_pass(text):
    return text is not None and not XFAIL.search(text)


# ---------- ratchet ----------
def ratchet(repo, ref=None):
    """Every way a ruler can get looser than at `ref`. Each line is one loosening."""
    ref = ref or base(repo)
    out = []
    was, cur = (json.loads(t) if t else None for t in (at(repo, ref, 'navlab.fitness.json'), now(repo, 'navlab.fitness.json')))
    if was:
        cur = cur or {}
        out += [f'fitness: accepted violation added: {v}' for v in sorted(set(cur.get('accepted', [])) - set(was.get('accepted', [])))]
        for k, v in was.get('budget', {}).items():
            if k not in cur.get('budget', {}) or cur['budget'][k] > v:  # every budget is a max_*: higher is looser
                out.append(f'fitness: budget {k} {v} -> {cur.get("budget", {}).get(k, "removed")}')
        for kind in ('imports', 'writers'):
            gone = {r.get('name') for r in was.get(kind, [])} - {r.get('name') for r in cur.get(kind, [])}
            out += [f'fitness: {kind} rule removed: {n}' for n in sorted(gone, key=str)]
        out += [f'fitness: rule turned off: {k}' for k, v in was.get('rules', {}).items() if v and not cur.get('rules', {}).get(k)]
    if (b := at(repo, ref, BASELINE)):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'was.json').write_text(b)
            Path(d, 'now.json').write_text(now(repo, BASELINE) or '{"contracts": {}}')
            p = subprocess.run([sys.executable, str(NAVLAB / 'arch' / 'contracts.py'), 'diff', f'{d}/was.json', f'{d}/now.json'],
                               capture_output=True, text=True)
            out += [f'contracts baseline: {l.strip()}' for l in p.stdout.splitlines() if l.startswith('  - ')] if p.returncode else []
    for f in (git(repo, 'ls-tree', '--name-only', ref, 'flows/') or '').splitlines():
        if f.endswith('.mjs') and must_pass(at(repo, ref, f)) and not must_pass(now(repo, f)):
            out.append(f'flows: must-pass flow {"deleted" if now(repo, f) is None else "marked xfail"}: {f}')
    for f in (git(repo, 'ls-tree', '--name-only', ref, 'flows/') or '').splitlines():
        if f.endswith('.mjs') and XFAIL.search(at(repo, ref, f) or '') and now(repo, f) is None:
            out.append(f'flows: xfail flow deleted (a demonstrated bug stays until it passes): {f}')
    o_was, o_cur = toml(at(repo, ref, '.orbit/orbit.toml')), toml(now(repo, '.orbit/orbit.toml'))
    for cid, c in o_was.get('checks', {}).items():
        n = o_cur.get('checks', {}).get(cid)
        if c.get('required') and c.get('command') and not (n and n.get('required') and n.get('command')):
            out.append(f'orbit.toml: required check {cid} {"removed" if not n else "made optional or planned"}')
        elif c.get('command') and n and n.get('command') != c['command']:
            out.append(f'orbit.toml: check {cid} command changed (the ruler moved in the same move)')
    for s, ids in o_was.get('check_sets', {}).items():
        gone = set(ids) - set(o_cur.get('check_sets', {}).get(s, []))
        out += [f'orbit.toml: check {i} dropped from set {s}' for i in sorted(gone)]
    p_was, p_cur = ({p['id']: p for p in toml(t).get('principle', [])} for t in (at(repo, ref, '.orbit/principles.toml'), now(repo, '.orbit/principles.toml')))
    for pid, p in p_was.items():
        if pid not in p_cur:
            out.append(f'principles: rule removed: {pid}')
        elif p.get('status') == 'enforced' and p_cur[pid].get('status') != 'enforced':
            out.append(f'principles: rule {pid} no longer enforced')
        elif p.get('predicate') != p_cur[pid].get('predicate'):
            out.append(f'principles: rule {pid} predicate changed')
    out += program_loosening(at(repo, ref, '.navlab/program.json'), now(repo, '.navlab/program.json'))
    for f in ('.claude/settings.json', '.claude/settings.local.json'):
        if '.orbit/gate stop' in (at(repo, ref, f) or '') and '.orbit/gate stop' not in (now(repo, f) or ''):
            out.append(f'{f}: gate Stop hook removed')
    return out


def program_loosening(was, cur):
    """The architecture program (navlab/arch/program.py) is a ruler too: an agent can't look done by moving a surface
    backwards, dropping it, unlinking a prediction or evidence, or rewriting its goal or judge. parked/stopped is allowed
    (program.py demands a why for it)."""
    if not was:
        return []
    sys.path.insert(0, str(NAVLAB / 'arch'))
    from program import STAGES, OFF
    was, cur = json.loads(was), json.loads(cur) if cur else {'surfaces': {}}
    out = [f'program: {k} changed' for k in ('goal', 'judge') if was.get(k) and cur.get(k) != was[k]]
    for name, w in was.get('surfaces', {}).items():
        c = cur.get('surfaces', {}).get(name)
        if not c:
            out.append(f'program: surface {name} removed')
            continue
        st = STAGES.get(w.get('move'), [])
        if w['stage'] in st and c['stage'] in st and st.index(c['stage']) < st.index(w['stage']):
            out.append(f'program: {name} moved back {w["stage"]} -> {c["stage"]}')
        out += [f'program: {name} {k} changed' for k in ('goal', 'judge', 'move') if w.get(k) and c.get(k) != w[k]]
        out += [f'program: {name} link removed: {l}' for l in sorted({l['ref'] for l in w.get('links', [])} - {l['ref'] for l in c.get('links', [])})]
        out += [f'program: {name} evidence removed: {k}' for k in sorted(set(w.get('evidence', {})) - set(c.get('evidence', {})))]
    return out


# ---------- checks ----------
def contracts(repo, init=False):
    nav = Path(repo) / '.navlab'
    nav.mkdir(exist_ok=True)
    for cmd in (['node', str(NAVLAB / 'arch' / 'facts.mjs'), str(repo), '--json', str(nav / 'facts.json')],
                [sys.executable, str(NAVLAB / 'arch' / 'contracts.py'), str(nav / 'facts.json'), '--json', str(nav / 'contracts.now.json')]):
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            print(p.stdout + p.stderr, file=sys.stderr)
            return 1
    if init:
        shutil.copy(nav / 'contracts.now.json', Path(repo) / BASELINE)
        print(f'wrote {BASELINE}: a person commits it (it is the ruler; the ratchet stops agents from shrinking it)')
        return 0
    if not (Path(repo) / BASELINE).exists():
        print(f'no {BASELINE}: run `.orbit/gate contracts --init` and commit it', file=sys.stderr)
        return 1
    return subprocess.run([sys.executable, str(NAVLAB / 'arch' / 'contracts.py'), 'diff', str(Path(repo) / BASELINE),
                           str(nav / 'contracts.now.json')]).returncode


def flows(repo, repeat=3, udid=None):
    sw = Path(os.environ.get('SIMWALK', HOME / 'simwalk' / 'simwalk.mjs'))
    files = sorted((Path(repo) / 'flows').glob('*.mjs'))
    must = [f for f in files if must_pass(f.read_text())]
    if not sw.exists():
        print(f'simwalk not found at {sw} (set SIMWALK)', file=sys.stderr)
        return 1
    if not must:
        print('no must-pass flows in flows/: a gate with nothing in it passes everything', file=sys.stderr)
        return 1
    udid = udid or os.environ.get('GATE_UDID')
    if not udid and shutil.which('orbitfleet'):
        print('set GATE_UDID to a simulator you claimed (orbitfleet claim); never a booted sim you did not claim', file=sys.stderr)
        return 1
    bad = 0
    for f in files:
        xf = not must_pass(f.read_text())
        p = subprocess.run(['node', str(sw), str(f), '--repeat', '1' if xf else str(repeat), *(['--udid', udid] if udid else [])],
                           cwd=repo, capture_output=True, text=True)
        verdict = ('PASSES NOW: bug fixed? a person drops the xfail marker' if p.returncode == 0 else 'fails (expected)') if xf \
            else ('PASS' if p.returncode == 0 else 'FAIL')
        bad += verdict == 'FAIL'
        print(f'{"xfail" if xf else "must "}  {verdict:<8}  {f.relative_to(repo)}')
        if verdict == 'FAIL':
            print('\n'.join('    ' + l for l in (p.stdout + p.stderr).strip().splitlines()[-12:]))
    return 1 if bad else 0


# ---------- the Stop hook ----------
def changed_files(repo, ref):
    names = set((git(repo, 'diff', '--name-only', ref) or '').splitlines())
    names |= set((git(repo, 'ls-files', '--others', '--exclude-standard') or '').splitlines())
    generated = ('.navlab/facts.json', '.navlab/contracts.now.json', '.navlab/navscan.json', '.orbit/runs/', '.orbit/.cache/')
    return [n for n in names if (Path(repo) / n).is_file() and not n.startswith(generated)]  # the gate's own output isn't a change


def stop_reasons(repo, verify):
    """Why this session may not stop yet. `verify(set)` runs an Orbit check set and returns (exit, failures)."""
    ref = base(repo)
    files = changed_files(repo, ref)
    if not files and git(repo, 'rev-parse', 'HEAD') == git(repo, 'rev-parse', ref):
        return []  # nothing changed since the base: nothing to gate
    cfg = toml(now(repo, '.orbit/orbit.toml'))
    sets = cfg.get('check_sets', {})
    if 'gate' not in sets:
        return ['.orbit/orbit.toml has no `gate` check set (run `.orbit/gate propose`)']
    code, fails = verify('gate')
    out = [f'verify gate: {f}' for f in fails] if code else []
    app = [f for f in files if not f.startswith(('.orbit/', '.navlab/', 'docs/')) and not f.endswith('.md')]
    if 'gate-device' in sets and app:
        newest = max((Path(repo) / f).stat().st_mtime for f in app)
        runs = Path(repo) / '.orbit' / cfg.get('project', {}).get('runs', 'runs')
        for cid in sets['gate-device']:
            if 'command' not in cfg.get('checks', {}).get(cid, {}):
                continue  # planned: nothing to run yet
            last = max(runs.glob(f'*-{cid}.json'), key=lambda p: p.stat().st_mtime, default=None)
            r = json.loads(last.read_text()) if last else {}
            if r.get('status') != 'passed' or last.stat().st_mtime < newest:
                out.append(f'gate-device: {cid} {"has not passed" if r.get("status") != "passed" else "is older than your last app change"}'
                           ' -> run `.orbit/orbit verify gate-device`')
    return out


def orbit_verify(repo):
    def run(s):
        p = subprocess.run([str(Path(repo) / '.orbit' / 'orbit'), 'verify', s, '--json'], cwd=repo, capture_output=True, text=True)
        try:
            o = json.loads(p.stdout)
        except json.JSONDecodeError:
            return 1, [(p.stdout + p.stderr).strip() or 'orbit verify did not run']
        bad = [r for r in o.get('results', []) if r['required'] and r['status'] in ('failed', 'error')]
        return o.get('exit', 1), [f'{r["check"]} {r["status"]}: {" | ".join([l.strip() for l in r["evidence"] if l.strip()][-5:])}' for r in bad]
    return run


def stop(repo, hook_input, verify=None):
    """Exit 2 + reasons on stderr blocks the agent's Stop; a second Stop in a row goes through and tells the user."""
    reasons = stop_reasons(repo, verify or orbit_verify(repo))
    if not reasons:
        return 0
    if hook_input.get('stop_hook_active'):
        print(json.dumps({'systemMessage': 'Gate still red for this session: ' + '; '.join(reasons)}))
        return 0
    print('The gate is red; fix this before stopping (or record the red in the task log and say why):', file=sys.stderr)
    print('\n'.join('  - ' + r for r in reasons), file=sys.stderr)
    print('Rulers are off-limits: loosening a baseline, budget, flow or required check fails `.orbit/gate ratchet`.', file=sys.stderr)
    return 2


# ---------- install / status / propose ----------
def install(repo):
    repo = Path(repo).resolve()
    d = repo / '.orbit'
    d.mkdir(exist_ok=True)
    (d / 'gate').write_text(LAUNCHER)
    (d / 'gate').chmod(0o755)
    climb = d / 'CLIMB.md'
    note = 'CLIMB.md present (kept)' if climb.exists() else 'CLIMB.md from template'
    if not climb.exists():
        climb.write_text((HOME / 'gate' / 'CLIMB.md').read_text())
    print(f'gate  installed  launcher .orbit/gate; {note}; next: gate skill (`.orbit/gate propose`)')
    return 0


def hooked(repo):
    return any('.orbit/gate stop' in (now(repo, f) or '') for f in ('.claude/settings.json', '.claude/settings.local.json'))


def status(repo):
    repo = Path(repo).resolve()
    if not (repo / '.orbit' / 'gate').exists():
        print('gate  not installed  next: orbit install --only gate')
    elif 'gate' not in toml(now(repo, '.orbit/orbit.toml')).get('check_sets', {}) or not hooked(repo):
        print('gate  installed  next: gate skill: `.orbit/gate propose`, add the checks to orbit.toml and the Stop hook')
    else:
        print('gate  online  next: online')
    return 0


def propose(repo):
    repo = Path(repo)
    cfg = toml(now(repo, '.orbit/orbit.toml'))
    have = cfg.get('checks', {})
    fast = [c for c in ('tsc', 'typecheck', 'lint', 'jest') if c in have]
    lines = ['# orbit-SP1 gate: review, then add to .orbit/orbit.toml (merge into an existing [check_sets]).']
    if not fast and (repo / 'tsconfig.json').exists():
        lines += ['[checks.tsc]', 'command = ["npx", "tsc", "--noEmit"]', 'required = true', 'timeout = 600']
        fast = ['tsc']
    lines += ['[checks.gate-ratchet]', 'command = [".orbit/gate", "ratchet"]', 'required = true',
              '[checks.gate-contracts]', 'command = [".orbit/gate", "contracts"]', 'required = true', 'timeout = 600',
              '[checks.gate-program]', 'command = [".orbit/gate", "program"]', 'required = true']
    fit = (repo / 'navlab.fitness.json').exists()
    lines += ['[checks.gate-fitness]'] + (['command = [".orbit/gate", "fitness"]', 'required = true', 'timeout = 600'] if fit else
                                         ['required = true   # planned: `fitness.py init` writes navlab.fitness.json (design-afresh, steward)'])
    fl = (repo / 'flows').is_dir()
    lines += ['[checks.gate-flows]'] + (['command = [".orbit/gate", "flows", "--repeat", "3"]', 'required = true', 'timeout = 1800'] if fl else
                                       ['required = true   # planned: write flows/*.mjs (argent-qa-flows / safety-net)'])
    lines += ['[check_sets]', 'gate = ' + json.dumps(fast + ['gate-ratchet', 'gate-contracts', 'gate-program', 'gate-fitness']), 'gate-device = ["gate-flows"]', '',
              '# .claude/settings.json (a person enables it; then the gate itself guards it):',
              '# ' + json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': HOOK, 'timeout': 900}]}]}})]
    if not (repo / BASELINE).exists():
        lines.append(f'# then: `.orbit/gate contracts --init` and commit {BASELINE}')
    print('\n'.join(lines))
    return 0


# ---------- selftest ----------
def selftest():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        g = lambda *a: subprocess.run(['git', '-C', t, '-c', 'user.email=t@t', '-c', 'user.name=t', *a], capture_output=True, check=True)
        g('init', '-q')
        (r / 'flows').mkdir()
        (r / '.orbit').mkdir()
        (r / 'flows' / 'checkout.mjs').write_text('export default async () => {}\n')
        (r / 'flows' / 'bug-123.mjs').write_text("export const xfail = 'double tap opens two sheets'\n")
        (r / 'navlab.fitness.json').write_text(json.dumps({'accepted': ['a'], 'budget': {'max_lag_ms': 16.7},
                                                           'imports': [{'name': 'no-ui-in-data'}], 'rules': {'inline_component': True}}))
        (r / '.orbit' / 'orbit.toml').write_text('[checks.tsc]\ncommand = ["tsc"]\nrequired = true\n[check_sets]\ngate = ["tsc"]\n'
                                                 'gate-device = ["gate-flows", "spectra"]\n[checks.gate-flows]\ncommand = ["x"]\nrequired = true\n[checks.spectra]\nrequired = true\n')
        (r / '.orbit' / 'principles.toml').write_text('[[principle]]\nid = "p1"\nstatus = "enforced"\npredicate = { kind = "max_loc", max = 300 }\n')
        (r / 'app.tsx').write_text('x\n')
        g('add', '-A')
        g('commit', '-qm', 'base')
        os.environ['GATE_BASE'] = 'HEAD'
        assert ratchet(r) == [], ratchet(r)
        # loosen every ruler once
        (r / 'navlab.fitness.json').write_text(json.dumps({'accepted': ['a', 'b'], 'budget': {'max_lag_ms': 20}, 'imports': [], 'rules': {}}))
        (r / 'flows' / 'checkout.mjs').write_text("export const xfail = 'flaky'\n")
        (r / 'flows' / 'bug-123.mjs').unlink()
        (r / '.orbit' / 'orbit.toml').write_text('[checks.tsc]\ncommand = ["tsc"]\nrequired = false\n[check_sets]\ngate = []\n')
        (r / '.orbit' / 'principles.toml').write_text('[[principle]]\nid = "p1"\nstatus = "observed"\npredicate = { kind = "max_loc", max = 300 }\n')
        got = ratchet(r)
        want = ['accepted violation added: b', 'budget max_lag_ms 16.7 -> 20', 'imports rule removed: no-ui-in-data', 'rule turned off: inline_component',
                'must-pass flow marked xfail: flows/checkout.mjs', 'xfail flow deleted', 'required check tsc made optional',
                'required check gate-flows removed', 'check tsc dropped from set gate', 'check gate-flows dropped from set gate-device', 'check spectra dropped from set gate-device',
                'rule p1 no longer enforced']
        assert all(any(w in l for l in got) for w in want) and len(got) == len(want), got
        # the architecture program is a ruler: stage regressions, dropped links/evidence, moved judges
        g('checkout', '-q', '--', '.')
        prog = {'judge': 'journey p50', 'surfaces': {'checkout': {'move': 'migrate', 'stage': 'building', 'judge': 'push_ms',
                'links': [{'ref': 'ledger:L1'}], 'evidence': {'contracts': 'c.json'}}, 'feed': {'move': 'tune', 'stage': 'climbing'}}}
        (r / '.navlab').mkdir()
        (r / '.navlab' / 'program.json').write_text(json.dumps(prog))
        g('add', '-A')
        g('commit', '-qm', 'program')
        back = json.loads(json.dumps(prog))
        back['surfaces']['checkout'].update(stage='designed', judge='vibes', links=[], evidence={})
        back['surfaces']['feed']['stage'] = 'stopped'  # stopping is allowed
        (r / '.navlab' / 'program.json').write_text(json.dumps(back))
        got = [l for l in ratchet(r) if l.startswith('program:')]
        assert got == ['program: checkout moved back building -> designed', 'program: checkout judge changed',
                       'program: checkout link removed: ledger:L1', 'program: checkout evidence removed: contracts'], got
        g('checkout', '-q', '--', '.')
        # tightening is not loosening
        g('checkout', '-q', '--', '.')
        (r / 'flows' / 'bug-123.mjs').write_text("export const xfail = 'double tap opens two sheets'\n")
        (r / 'navlab.fitness.json').write_text(json.dumps({'accepted': [], 'budget': {'max_lag_ms': 12}, 'imports': [{'name': 'no-ui-in-data'}],
                                                           'rules': {'inline_component': True}}))
        assert ratchet(r) == [], ratchet(r)
        # stop hook: red verify blocks once, the second Stop goes through with a message
        red = lambda s: (1, ['tsc failed: src/a.tsx:3 error'])
        assert stop(r, {}, red) == 2 and stop(r, {'stop_hook_active': True}, red) == 0
        # device checks: no passing run -> blocked; a passing run newer than the app change -> clear
        green = lambda s: (0, [])
        (r / 'app.tsx').write_text('y\n')
        assert any('gate-flows has not passed' in x for x in stop_reasons(r, green)), stop_reasons(r, green)
        (r / '.orbit' / 'runs').mkdir()
        time.sleep(0.01)
        (r / '.orbit' / 'runs' / '20261026T000000Z-gate-flows.json').write_text('{"status": "passed"}')
        assert stop_reasons(r, green) == [], stop_reasons(r, green)
        os.utime(r / 'app.tsx')  # an app change after the run makes it stale
        assert any('older than your last app change' in x for x in stop_reasons(r, green))
        # nothing changed since the base: never gated
        g('checkout', '-q', '--', '.')
        g('clean', '-fdq')
        assert stop_reasons(r, red) == [], stop_reasons(r, red)
        # install: idempotent, CLIMB.md kept
        install(r)
        (r / '.orbit' / 'CLIMB.md').write_text('mine\n')
        install(r)
        assert (r / '.orbit' / 'CLIMB.md').read_text() == 'mine\n'
    print('selftest ok')
    return 0


def main(a):
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    pos = [x for i, x in enumerate(a) if not x.startswith('--') and (i == 0 or a[i - 1] not in ('--repo', '--repeat', '--udid'))]
    cmd = pos[0] if pos else 'help'
    repo = Path(opt('--repo') or (pos[1] if len(pos) > 1 else '.')).resolve()
    if cmd == 'selftest':
        return selftest()
    if cmd == 'install':
        return install(repo)
    if cmd == 'status':
        return status(repo)
    if cmd == 'propose':
        return propose(repo)
    if cmd == 'ratchet':
        out = ratchet(repo)
        print('\n'.join(out) if out else f'ratchet ok (no ruler looser than {base(repo)[:12]})')
        return 1 if out else 0
    if cmd == 'fitness':
        return subprocess.run([sys.executable, str(NAVLAB / 'arch' / 'fitness.py'), 'check', str(repo)]).returncode
    if cmd == 'program':
        return subprocess.run([sys.executable, str(NAVLAB / 'arch' / 'program.py'), 'verify', '--repo', str(repo)]).returncode
    if cmd == 'contracts':
        return contracts(repo, '--init' in a)
    if cmd == 'flows':
        return flows(repo, int(opt('--repeat', 3)), opt('--udid'))
    if cmd == 'stop':
        raw = sys.stdin.read() if not sys.stdin.isatty() else ''
        return stop(repo, json.loads(raw) if raw.strip() else {})
    print(__doc__)
    return 0 if cmd == 'help' else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
