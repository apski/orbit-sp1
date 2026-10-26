#!/usr/bin/env python3
"""program: where every surface of the architecture program stands, and the cheapest way back in.

  program.py init [<repo>] [--goal G --judge J]          .navlab/program.json (the program's goal + its outside judge)
  program.py add <surface> --move migrate|redesign|tune|lab [--entry f ...] [--unit U] [--goal G --judge J]
  program.py set <surface> key=value ...                 evidence (path, ledger:<id>, or a note) and fields (unit, owner, next)
  program.py link <surface> <kind>:<ref> [--note "..."]   ledger:<id> voyager:<job> gap:<id> decision:<path> file:<path>
  program.py ready <surface>                             native-port readiness gate: the 10 items, exit 1 if 2/5/6 missing
  program.py advance <surface> <stage> [--why "..."]     move forward only when the stage's check passes
  program.py status [--zoom 0|1|2] [--surface S] [--task T] [--budget N] [--json]
  program.py verify                                      every surface's stage still holds (evidence resolves, history
                                                         records it): catches a hand-edit that skipped a check; gate check
  program.py selftest

State is one row per surface: its move, stage, goal and judge (set once, by the `ambitious` pass), evidence and links.
Links are pointers into each tool's own store (ledger, Voyager jobs, gaps, gate runs, docs); status reads them live,
so nothing is copied and nothing goes stale. Status is deterministic and cites its sources: zoom 0 = the program in
~300 tokens, 1 = one surface, 2 = one task (Orbit packet + orbit-rag bundle, budgeted).

Stages only move forward, through a check; backwards needs --why and is recorded. `stopped`/`parked` need --why.
The gate's ratchet (gate/gate.py) treats a stage regression, a dropped surface or link, or a changed goal/judge
against the base commit as loosening.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAVLAB = HERE.parent
STATE = '.navlab/program.json'
STAGES = {
    'migrate': ['candidate', 'ranked', 'ready', 'designed', 'seamed', 'building', 'measuring', 'done'],
    'redesign': ['candidate', 'ready', 'designed', 'migrating', 'measuring', 'done'],
    'tune': ['candidate', 'climbing', 'measuring', 'done'],
    'lab': ['candidate', 'running', 'done'],
}
OFF = ('parked', 'stopped')
QUIET = ('candidate', 'ranked', 'ready', 'done') + OFF  # everything else is "in flight" (steward: at most 3)
# native-port readiness: key, question, required
READY = [('gap', 'remaining RN-vs-native gap after structure/content fixes (twin, model or journey)', False),
         ('contracts', 'contracts inventory for the surface (contracts-scan --scope)', True),
         ('state', 'state read on both sides of the seam (data-layer-audit)', False),
         ('seam', 'crossing share for the candidate split (replay.py --map)', False),
         ('safety', 'e2e flows that would fail on a broken journey (safety-net)', True),
         ('baseline', 'Release baseline measured: a ledger prediction (ledger:<id>) or trace', True),
         ('release', 'store vs OTA share for the seam (release-boundary)', False),
         ('ds', 'design-system parity: components with no native counterpart (ds-audit)', False),
         ('platform', 'RN version, New Architecture, min iOS (upgrade-readiness)', False),
         ('team', 'who owns Swift (a name; "none" fails)', False)]
CHECKS = {  # (move, stage) -> evidence that must resolve before entering it
    ('migrate', 'ranked'): ['rank'], ('migrate', 'designed'): ['design'], ('migrate', 'seamed'): ['seam'],
    ('redesign', 'designed'): ['design'],
}
STALE_DAYS = 30


def j(p, d=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return d


def jl(p):
    try:
        return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]
    except OSError:
        return []


class Program:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.path = self.repo / STATE
        self.d = j(self.path, None)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d, indent=1, sort_keys=True) + '\n')

    def need(self):
        if self.d is None:
            sys.exit(f'no {STATE}: run `program.py init` (native-port / app-architecture-steward skill)')
        return self.d

    def surface(self, s):
        try:
            return self.need()['surfaces'][s]
        except KeyError:
            sys.exit(f'unknown surface {s!r}; known: {", ".join(sorted(self.d["surfaces"])) or "none"}')

    # ---------- evidence ----------
    def ledger(self):
        return {r['id']: r for r in jl(self.repo / '.navlab' / 'ledger.jsonl')}

    def resolve(self, v):
        """(ok, label): a path that exists, a ledger id that exists, else a note (present, but not evidence)."""
        if not isinstance(v, str) or not v:
            return False, 'missing'
        if v.startswith('ledger:'):
            e = self.ledger().get(v[7:])
            return (True, f'{v} ({e["metric"]} {e["baseline"]})') if e else (False, f'{v} not in .navlab/ledger.jsonl')
        if v.lower() in ('none', 'unknown', 'n/a'):
            return False, v
        if '/' in v or v.endswith(('.json', '.md', '.jsonl')):
            p = self.repo / v
            return (True, v) if p.exists() else (False, f'{v} (file missing)')
        return True, f'note: {v}'

    def readiness(self, row):
        out = []
        for key, q, req in READY:
            ok, label = self.resolve(row.get('evidence', {}).get(key))
            if key == 'safety' and ok and label.endswith('.json'):
                s = j(self.repo / label, {}).get('screens', [])
                cov = sum(x.get('status') == 'covered' for x in s)
                ok, label = cov > 0, f'{label} ({cov}/{len(s)} screens covered)'
            if req and label.startswith('note:'):
                ok, label = False, label + ' (a required item needs a file or ledger id, not a note)'
            out.append((key, q, req, ok, label))
        return out

    def link_state(self, link):
        kind, _, ref = link['ref'].partition(':')
        if kind == 'ledger':
            e = self.ledger().get(ref)
            if not e:
                return 'missing', f'{ref} not in ledger'
            if e['status'] == 'open':
                age = (time.time() - time.mktime(time.strptime(e['date'], '%Y-%m-%d'))) / 86400
                return ('stale' if age > STALE_DAYS else 'open'), f'{e["metric"]} {e["baseline"]}→? (pred {e["predicted"]} [{e["lo"]}, {e["hi"]}], {age:.0f}d)'
            if e['status'] == 'withdrawn':
                return 'withdrawn', e.get('why', '')
            hit = e['lo'] <= e['actual'] <= e['hi']
            return ('hit' if hit else 'miss'), f'{e["metric"]} {e["baseline"]}→{e["actual"]} (pred {e["predicted"]}, {"HIT" if hit else "MISS"})'
        if kind == 'voyager':
            st = next(iter(sorted(Path.home().glob(f'.voyager/*/jobs/{ref}/state.json'))), None)
            s = j(st, {}) if st else {}
            return (s.get('step', 'missing'), 'escalated' if s.get('escalated') else (s.get('goal', '') or '')[:60]) if s else ('missing', 'no job state')
        if kind == 'gap':
            g = next((r for r in jl(NAVLAB / 'learning' / 'gaps.jsonl') if r['id'] == ref), None)
            return (g['status'], g['what'][:70]) if g else ('missing', 'not in gaps.jsonl')
        p = self.repo / ref
        if not p.exists():
            return 'missing', f'{ref} (file missing)'
        head = next((l.lstrip('# ').strip() for l in p.read_text(errors='ignore').splitlines() if l.startswith('#')), '') if p.suffix == '.md' else ''
        return 'ok', (head or ref)[:70]

    def gate(self):
        """Latest Orbit verify of the `gate` set: green | red | none."""
        runs = self.repo / '.orbit' / 'runs'
        cfg = self.repo / '.orbit' / 'orbit.toml'
        if not cfg.exists() or not runs.exists():
            return 'none'
        try:
            import tomllib
            ids = tomllib.loads(cfg.read_text()).get('check_sets', {}).get('gate', [])
        except Exception:
            return 'none'
        last = {}
        for f in sorted(runs.glob('*.json')):
            r = j(f, {})
            if r.get('check') in ids:
                last[r['check']] = r
        if not last:
            return 'none'
        return 'red' if any(r['required'] and r['status'] in ('failed', 'error') for r in last.values()) else 'green'

    # ---------- transitions ----------
    def advance(self, s, to, why=None, by='agent'):
        row = self.surface(s)
        stages = STAGES[row['move']]
        cur = row['stage']
        if to in OFF:
            if not why:
                return [f'{to} needs --why']
        elif to not in stages:
            return [f'{to} is not a {row["move"]} stage: {" → ".join(stages)}']
        elif cur not in OFF and stages.index(to) < stages.index(cur) and not why:
            return [f'{cur} → {to} goes backwards: give --why (recorded; the gate ratchet flags it against the base)']
        elif cur not in OFF and stages.index(to) > stages.index(cur) + 1:
            return [f'{cur} → {to} skips {", ".join(stages[stages.index(cur) + 1:stages.index(to)])}: one stage at a time']
        else:
            bad = self.check(s, row, to)
            if bad:
                return bad
        row.setdefault('history', []).append({'date': time.strftime('%Y-%m-%d'), 'from': cur, 'to': to, 'by': by, **({'why': why} if why else {})})
        row['stage'] = to
        self.save()
        return []

    def check(self, s, row, to):
        bad = []
        for key in CHECKS.get((row['move'], to), []):
            ok, label = self.resolve(row.get('evidence', {}).get(key))
            if not ok or label.startswith('note:'):
                bad.append(f'{to} needs evidence `{key}` (a file or ledger id): {label}')
        if to == 'ready':
            bad += [f'readiness {k}: {label}' for k, _, req, ok, label in self.readiness(row) if req and not ok]
        if to in ('building', 'climbing', 'running', 'migrating'):  # acceptance first: a prediction before the code
            if not any(self.link_state(l)[0] in ('open', 'stale') for l in row.get('links', []) if l['ref'].startswith('ledger:')):
                bad.append(f'{to} needs an open ledger prediction linked first (ledger.py add, then `link {s} ledger:<id>`)')
        if to == 'measuring' and self.gate() == 'red':
            bad.append('measuring needs a green gate (`.orbit/orbit verify gate`)')
        if to == 'done':
            led = [l for l in row.get('links', []) if l['ref'].startswith('ledger:')]
            st = [self.link_state(l)[0] for l in led]
            if not led or any(x in ('open', 'stale', 'missing') for x in st):
                bad.append('done needs every linked prediction measured (`ledger.py measure <id>`)')
        return bad

    def verify(self):
        """What a surface's current stage needs must still be true now; a stage reached by hand-editing the file (no
        history entry) fails. Not re-checked: 'open prediction' (it gets measured later) and 'gate green' (circular)."""
        bad = []
        for s, row in self.need()['surfaces'].items():
            st = STAGES[row['move']]
            if (row.get('history') or [{}])[-1].get('to') != row['stage']:
                bad.append(f'{s}: stage {row["stage"]} has no history entry (edited by hand, not `program.py advance`)')
            if row['stage'] in OFF:
                continue
            reached = st[1:st.index(row['stage']) + 1]
            for to in reached:
                bad += [f'{s} ({row["stage"]}): {b}' for b in self.check(s, row, to)
                        if 'open ledger prediction' not in b and 'green gate' not in b and to != 'done']
            if any(t in reached for t in ('building', 'climbing', 'running', 'migrating')) and not any(
                    self.link_state(l)[0] not in ('missing', 'withdrawn') for l in row.get('links', []) if l['ref'].startswith('ledger:')):
                bad.append(f'{s} ({row["stage"]}): no linked prediction left')
            if row['stage'] == 'done':
                bad += [f'{s} (done): {b}' for b in self.check(s, row, 'done')]
        return bad

    # ---------- status ----------
    def flags(self, row):
        f = []
        for l in row.get('links', []):
            k, note = self.link_state(l)
            if k == 'stale':
                f.append(f'prediction {l["ref"][7:]} older than {STALE_DAYS}d')
            if k == 'missing':
                f.append(f'broken link {l["ref"]}')
            if l['ref'].startswith('gap:') and k in ('open', 'candidate', 'unpinned'):
                f.append(f'blocked on {l["ref"]}')
            if l['ref'].startswith('voyager:') and note == 'escalated':
                f.append(f'job {l["ref"][8:]} escalated')
        return f

    def headline(self, row):
        """The last measured number, else the open prediction."""
        best = None
        for l in row.get('links', []):
            if l['ref'].startswith('ledger:'):
                k, note = self.link_state(l)
                if k in ('hit', 'miss') or (best is None and k in ('open', 'stale')):
                    best = note
        return best or 'no prediction yet'

    def program_flags(self):
        d = self.need()
        f = []
        flight = [s for s, r in d['surfaces'].items() if r['stage'] not in QUIET]
        if len(flight) > 3:
            f.append(f'{len(flight)} in flight (> 3): finish before starting ({", ".join(flight)})')
        measured = sorted((e for e in self.ledger().values() if e['status'] == 'measured' and e.get('move') == 'migrate'),
                          key=lambda e: e.get('measured', e['date']))
        if len(measured) >= 2 and all(not (e['lo'] <= e['actual'] <= e['hi']) for e in measured[-2:]):
            f.append('STOP RULE: the last two migrate predictions missed: pause the program and re-plan (rn-native-migration)')
        if self.gate() == 'red':
            f.append('gate red')
        return f

    def zoom0(self):
        d = self.need()
        S = d['surfaces']
        n = lambda st: sum(r['stage'] in st for r in S.values())
        led = [e for e in self.ledger().values() if e['status'] == 'measured']
        hits = sum(e['lo'] <= e['actual'] <= e['hi'] for e in led)
        out = [f'program: {d.get("goal") or "(no goal: init --goal)"}  judge: {d.get("judge") or "(none)"}',
               f'{len(S)} surfaces: {n(("candidate", "ranked", "ready"))} queued, {len(S) - n(QUIET)} in flight, {n(("done",))} done, '
               f'{n(OFF)} parked/stopped | gate {self.gate()} | ledger {hits}/{len(led)} hit']
        out += [f'!! {x}' for x in self.program_flags()]
        order = {s: i for i, s in enumerate(['measuring', 'building', 'seamed', 'designed', 'migrating', 'climbing', 'running',
                                              'ready', 'ranked', 'candidate', 'done', 'parked', 'stopped'])}
        for s, r in sorted(S.items(), key=lambda kv: (order.get(kv[1]['stage'], 99), kv[0])):
            fl = self.flags(r)
            out.append(f'{s:<14} {r["move"]:<8} {r["stage"]:<10} {self.headline(r)}  next: {r.get("next") or self.next_step(s, r)}'
                       + (f'  ⚠ {"; ".join(fl)}' if fl else ''))
        out.append(f'(source: {STATE}, .navlab/ledger.jsonl, .orbit/runs; zoom 1: status --zoom 1 --surface <s>)')
        return out

    def next_step(self, s, r):
        st = STAGES[r['move']]
        if r['stage'] in OFF or r['stage'] == 'done':
            return '-'
        to = st[st.index(r['stage']) + 1]
        bad = self.check(s, r, to)
        return f'advance {to}' if not bad else bad[0]

    def zoom1(self, s):
        r = self.surface(s)
        d = self.need()
        inh = lambda k: r.get(k) or (f'(program) {d[k]}' if d.get(k) else '(none: run the ambitious pass)')
        out = [f'# {s}  ({r["move"]}, stage {r["stage"]})', f'goal: {inh("goal")}', f'judge: {inh("judge")}', f'entry: {", ".join(r.get("entry", [])) or "-"}  unit: {r.get("unit") or "-"}  owner: {r.get("owner") or "-"}']
        if r['move'] == 'migrate':
            out.append('readiness:')
            out += [f'  {"✓" if ok else ("✗" if req else "·")} {k:<9} {label}' + ('' if ok else f'   <- {q}') for k, q, req, ok, label in self.readiness(r)]
        other = {k: v for k, v in r.get('evidence', {}).items() if k not in {x[0] for x in READY}}
        out += [f'evidence {k}: {self.resolve(v)[1]}' for k, v in sorted(other.items())]
        out.append('links:')
        out += [f'  {l["ref"]:<40} {"  ".join(self.link_state(l))}' + (f'  ({l["note"]})' if l.get('note') else '') for l in r.get('links', [])] or ['  none']
        out += [f'⚠ {x}' for x in self.flags(r)]
        h = r.get('history', [])[-3:]
        out += ['history: ' + '; '.join(f'{x["date"]} {x["from"] or "added"}→{x["to"]}' + (f' ({x["why"]})' if x.get('why') else '') for x in h)] if h else []
        out.append(f'next: {r.get("next") or self.next_step(s, r)}')
        rag = rag_bundle(self.repo, f'{s} {r["move"]} decisions', 400)
        out += ['decisions (orbit-rag, human-approved first):'] + ['  ' + l for l in rag]
        return out

    def zoom2(self, s, task, budget):
        r = self.surface(s)
        out = [f'# task: {task}  (surface {s}, {r["stage"]})']
        orbit = self.repo / '.orbit' / 'orbit'
        if r.get('unit') and orbit.exists():
            p = subprocess.run([str(orbit), 'packet', r['unit'], task, '--budget', str(budget // 2)], cwd=self.repo, capture_output=True, text=True)
            out += ['## code (orbit packet)'] + (p.stdout.strip().splitlines() or [p.stderr.strip()])
        else:
            out.append(f'## code: set the surface unit (`set {s} unit=<orbit unit>`) and run `.orbit/orbit packet <unit> "{task}"`')
        out += ['## docs and decisions (orbit-rag)'] + rag_bundle(self.repo, task, budget // 2)
        return out


def rag_bundle(repo, task, tokens):
    exe = Path.home() / '.local' / 'share' / 'orbit-rag' / 'bin' / 'orbit-rag'
    cmd = [str(exe), 'context', 'bundle', '--task', task, '--tokens', str(tokens)]
    if not exe.exists() or not (Path(repo) / '.orbit-rag').exists():
        return [f'(orbit-rag not set up here: `orbit install --only orbit-rag`, then `{" ".join(cmd[1:])}`)']
    try:
        p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=60)
        return p.stdout.strip().splitlines() or [p.stderr.strip()]
    except subprocess.TimeoutExpired:
        return ['(orbit-rag timed out)']


def budgeted(lines, budget):
    out, used = [], 0
    for i, l in enumerate(lines):
        t = len(l) // 4 + 1
        if used + t > budget:
            out.append(f'… {len(lines) - i} more lines over the {budget}-token budget (raise --budget or zoom in)')
            break
        out.append(l)
        used += t
    return out


def main(a):
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    vals = ('--goal', '--judge', '--move', '--entry', '--unit', '--note', '--why', '--zoom', '--surface', '--task', '--budget', '--repo', '--by')
    pos = [x for i, x in enumerate(a) if not x.startswith('--') and (i == 0 or a[i - 1] not in vals)]
    cmd = pos[0] if pos else 'help'
    P = Program(opt('--repo', '.'))
    if cmd == 'selftest':
        return selftest()
    if cmd == 'init':
        if len(pos) > 1:
            P = Program(pos[1])
        P.d = P.d or {'version': 1, 'surfaces': {}}
        for k in ('goal', 'judge'):
            if opt(f'--{k}'):
                P.d[k] = opt(f'--{k}')
        P.save()
        print(f'{STATE}: {len(P.d["surfaces"])} surfaces')
        return 0
    if cmd == 'add':
        s, mv = pos[1], opt('--move', 'migrate')
        if mv not in STAGES:
            sys.exit(f'--move must be one of {", ".join(STAGES)}')
        d = P.need()
        if s in d['surfaces']:
            sys.exit(f'{s} exists (stage {d["surfaces"][s]["stage"]})')
        d['surfaces'][s] = {'move': mv, 'stage': 'candidate', 'entry': [a[i + 1] for i, x in enumerate(a) if x == '--entry'],
                            'unit': opt('--unit'), 'goal': opt('--goal'), 'judge': opt('--judge'), 'evidence': {}, 'links': [],
                            'history': [{'date': time.strftime('%Y-%m-%d'), 'from': None, 'to': 'candidate', 'by': opt('--by', 'agent')}]}
        P.save()
        print(f'added {s} ({mv}, candidate)')
        return 0
    if cmd == 'set':
        r = P.surface(pos[1])
        for kv in pos[2:]:
            k, _, v = kv.partition('=')
            if k in ('unit', 'owner', 'next', 'goal', 'judge'):
                if k in ('goal', 'judge') and r.get(k) and r[k] != v:
                    print(f'note: {k} changed from {r[k]!r}: the gate ratchet flags a moved judge against the base', file=sys.stderr)
                r[k] = v
            else:
                r.setdefault('evidence', {})[k] = v
                print(f'{k}: {P.resolve(v)[1]}')
        P.save()
        return 0
    if cmd == 'link':
        r = P.surface(pos[1])
        ref = pos[2]
        if ':' not in ref:
            sys.exit('link ref is <kind>:<ref> (ledger:, voyager:, gap:, decision:, file:)')
        if ref.split(':')[0] in ('decision', 'file'):
            ref = 'file:' + ref.split(':', 1)[1]
        if all(l['ref'] != ref for l in r.setdefault('links', [])):
            r['links'].append({'ref': ref, **({'note': opt('--note')} if opt('--note') else {})})
        P.save()
        print(f'{pos[1]} -> {ref}: {"  ".join(P.link_state({"ref": ref}))}')
        return 0
    if cmd == 'ready':
        r = P.surface(pos[1])
        rows = P.readiness(r)
        for k, q, req, ok, label in rows:
            print(f'{"✓" if ok else ("✗" if req else "·")} {k:<9} {label}' + ('' if ok else f'   <- {q}'))
        bad = [k for k, _, req, ok, _ in rows if req and not ok]
        print(f'\nready: {"NO (missing " + ", ".join(bad) + ")" if bad else "yes"}  ({sum(x[3] for x in rows)}/{len(rows)} items with evidence)')
        return 1 if bad else 0
    if cmd == 'advance':
        bad = P.advance(pos[1], pos[2], opt('--why'), opt('--by', 'agent'))
        print('\n'.join(f'refused: {b}' for b in bad) if bad else f'{pos[1]}: now {pos[2]}')
        return 1 if bad else 0
    if cmd == 'verify':
        if P.d is None:
            print(f'no {STATE}: nothing to verify')
            return 0
        bad = P.verify()
        print('\n'.join(bad) if bad else f'program ok: {len(P.d["surfaces"])} surfaces hold their stages')
        return 1 if bad else 0
    if cmd == 'status':
        z, s = int(opt('--zoom', 0)), opt('--surface')
        if z and not s:
            sys.exit('--zoom 1/2 needs --surface')
        if '--json' in a:
            d = P.need()
            print(json.dumps({'flags': P.program_flags(), 'gate': P.gate(), 'surfaces': {k: {**r, 'headline': P.headline(r), 'flags': P.flags(r),
                              'next': r.get('next') or P.next_step(k, r)} for k, r in d['surfaces'].items()}}, indent=1))
            return 0
        lines = P.zoom0() if z == 0 else P.zoom1(s) if z == 1 else P.zoom2(s, opt('--task', s), int(opt('--budget', 3000)))
        print('\n'.join(budgeted(lines, int(opt('--budget', {0: 400, 1: 1500, 2: 3000}[min(z, 2)])))))
        return 0
    print(__doc__)
    return 0 if cmd == 'help' else 2


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / '.navlab').mkdir()
        run = lambda *a: main([*a, '--repo', t])
        import contextlib
        with contextlib.redirect_stdout(open(os.devnull, 'w')):
            assert run('init', t, '--goal', 'hot paths native-fast', '--judge', 'journey.py p50, Release') == 0
            assert run('add', 'checkout', '--move', 'migrate', '--entry', 'src/checkout/Nav.tsx', '--unit', 'rn:checkout') == 0
            P = Program(t)
            assert P.advance('checkout', 'ready') and P.advance('checkout', 'ranked'), 'skip and missing rank must refuse'
            (r / '.navlab' / 'portfolio.json').write_text('{}')
            run('set', 'checkout', 'rank=.navlab/portfolio.json', 'team=core')
            assert Program(t).advance('checkout', 'ranked') == []
            bad = Program(t).advance('checkout', 'ready')
            assert any('contracts' in b for b in bad) and any('safety' in b for b in bad) and any('baseline' in b for b in bad), bad
            # a note never satisfies a required item
            run('set', 'checkout', 'contracts=looks fine to me')
            assert any('contracts' in b for b in Program(t).advance('checkout', 'ready'))
            (r / '.navlab' / 'checkout-contracts.json').write_text('{}')
            (r / '.navlab' / 'checkout-safetynet.json').write_text(json.dumps({'screens': [{'status': 'uncovered'}]}))
            (r / '.navlab' / 'ledger.jsonl').write_text(json.dumps({'id': 'L1', 'date': time.strftime('%Y-%m-%d'), 'feature': 'checkout', 'move': 'migrate',
                'metric': 'push_ms', 'baseline': 92, 'predicted': 70, 'lo': 62, 'hi': 78, 'status': 'open', 'actual': None}) + '\n')
            run('set', 'checkout', 'contracts=.navlab/checkout-contracts.json', 'safety=.navlab/checkout-safetynet.json', 'baseline=ledger:L1')
            assert any('0/1 screens covered' in b for b in Program(t).advance('checkout', 'ready')), 'uncovered safety net must refuse'
            (r / '.navlab' / 'checkout-safetynet.json').write_text(json.dumps({'screens': [{'status': 'covered'}, {'status': 'uncovered'}]}))
            assert Program(t).advance('checkout', 'ready') == []
            (r / 'docs').mkdir()
            (r / 'docs' / 'checkout.md').write_text('# Checkout afresh\n')
            (r / '.navlab' / 'split.json').write_text('{}')
            run('set', 'checkout', 'design=docs/checkout.md', 'seam=.navlab/split.json')
            P = Program(t)
            assert P.advance('checkout', 'designed') == [] and P.advance('checkout', 'seamed') == []
            assert any('ledger prediction' in b for b in P.advance('checkout', 'building')), 'no code before a prediction'
            run('link', 'checkout', 'ledger:L1')
            P = Program(t)
            assert P.advance('checkout', 'building') == [] and P.advance('checkout', 'measuring') == []
            assert Program(t).verify() == [], Program(t).verify()
            # a hand-edit that jumps a stage, or evidence that stops resolving, fails verify
            p = Program(t)
            p.d['surfaces']['checkout']['stage'] = 'done'
            assert any('no history entry' in b for b in p.verify())
            (r / '.navlab' / 'split.json').unlink()
            assert any('seam' in b for b in Program(t).verify()), Program(t).verify()
            (r / '.navlab' / 'split.json').write_text('{}')
            assert any('measured' in b for b in P.advance('checkout', 'done')), 'done needs the prediction measured'
            assert P.advance('checkout', 'designed') and P.advance('checkout', 'stopped'), 'backwards and stopped need --why'
            # status: zoom 0 headline, stale + stop-rule flags, zoom 1 readiness, budget truncation
            rows = [json.loads(l) for l in (r / '.navlab' / 'ledger.jsonl').read_text().splitlines()]
            rows[0]['date'] = '2026-01-01'
            rows += [dict(rows[0], id=f'M{i}', status='measured', actual=95.0, date='2026-10-0' + str(i)) for i in (1, 2)]
            (r / '.navlab' / 'ledger.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in rows))
            P = Program(t)
            z0 = '\n'.join(P.zoom0())
            assert 'push_ms 92→? (pred 70' in z0 and 'older than 30d' in z0 and 'STOP RULE' in z0 and 'ledger 0/2 hit' in z0, z0
            z1 = '\n'.join(P.zoom1('checkout'))
            assert '✓ contracts' in z1 and '1/2 screens covered' in z1 and '· gap' in z1, z1
            assert len(budgeted(P.zoom1('checkout'), 40)) < len(P.zoom1('checkout'))
            for i in range(4):
                run('add', f's{i}', '--move', 'tune')
                p = Program(t)
                p.d['surfaces'][f's{i}']['stage'] = 'climbing'
                p.save()
            assert any('in flight (> 3)' in f for f in Program(t).program_flags())
    print('selftest ok')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
