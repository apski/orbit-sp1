#!/usr/bin/env python3
"""featuremodel: an explicit, checkable model of one feature, the basis for boundaries in design-afresh.

  featuremodel.py scaffold --facts facts.json --scope depgraph.json --name <feature> --out model.json
  featuremodel.py check model.json --repo <repo> [--facts facts.json --scope depgraph.json]

scaffold writes the facts the tools can see (units with their reads/writes/API/navigation/imports, plus every contract
in scope) and empty sections the agent fills by reading code:
  concepts          domain nouns, each with evidence (file:line)
  state             each piece of state with exactly ONE owner (a unit, or server:<operation>) and its readers
  responsibilities  what the feature does, each mapped to units, with cites
  flows             user journeys as ordered responsibility ids
  dropped           accidental growth deliberately left out (dead flags, duplicates), each with why
check refuses a model that doesn't explain the code: units not placed in any responsibility or dropped; state with zero
or several owners; references to unknown units or responsibilities; cites that point at missing files or lines; and
contracts in scope that the model never mentions (an API op, storage key, route, event or flag that would be lost).
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contracts import inventory  # noqa: E402


def unit_kind(f, name):
    if re.search(r'store', f, re.I):
        return 'store'
    if name.startswith('use'):
        return 'hook'
    if re.search(r'(service|api|client|query|mutation|graphql)', f, re.I):
        return 'data'
    return 'component' if name[:1].isupper() else 'module'


def scaffold(a, opt):
    facts = json.load(open(opt('--facts')))
    dep = json.load(open(opt('--scope')))
    owned = {o['file'] for o in dep['owned']}
    stem = lambda f: os.path.splitext(os.path.basename(f))[0]
    file_units = {}
    units = []
    for f in facts['facts']:
        if f['file'] not in owned or f.get('test'):
            continue
        names = [c['name'] for c in f['components']] or [stem(f['file'])]
        for n in names:
            uid = n if not any(u['id'] == n for u in units) else f'{n}@{stem(f["file"])}'
            line = next((c['line'] for c in f['components'] if c['name'] == n), 1)
            units.append({'id': uid, 'file': f['file'], 'line': line, 'kind': unit_kind(f['file'], n),
                          'reads': sorted({f"{r['hook']}({r['arg']})" for r in f['state']['reads']}), 'writes': f['state']['writes'],
                          'api': sorted({f"{x.get('op') or x.get('method') or x['kind']} {x['target']}" for x in f['api']}),
                          'nav': sorted({x['route'] for x in f['nav']}), 'imports': []})
            file_units.setdefault(f['file'], []).append(uid)
    for a_, b in dep.get('edges', []):
        for u in file_units.get(a_, []):
            for v in file_units.get(b, []):
                next(x for x in units if x['id'] == u)['imports'].append(v)
    model = {'feature': opt('--name', 'feature'), 'repo': facts['repo'], 'generated': time.strftime('%Y-%m-%d'),
             'units': units, 'contracts': inventory(facts, owned),
             'concepts': [{'name': 'TODO', 'evidence': ['file:line']}],
             'state': [{'name': 'TODO', 'owner': 'unit id | server:<op>', 'readers': [], 'cite': 'file:line'}],
             'responsibilities': [{'id': 'TODO', 'description': '', 'units': [], 'cite': ['file:line']}],
             'flows': [{'name': 'TODO', 'steps': [], 'cite': []}],
             'dropped': []}
    json.dump(model, open(opt('--out'), 'w'), indent=1)
    print(f'scaffold: {len(units)} units, {sum(len(v) for v in model["contracts"].values())} contracts -> {opt("--out")}; '
          f'fill concepts/state/responsibilities/flows/dropped by reading the code, then run check')


def check(a, opt):
    m = json.load(open(a[1]))
    repo = opt('--repo', m.get('repo'))
    errors, warns = [], []
    units = {u['id'] for u in m['units']}
    resp = {r['id']: r for r in m['responsibilities'] if r['id'] != 'TODO'}
    dropped_txt = json.dumps(m.get('dropped', []))
    placed = {u for r in resp.values() for u in r['units']}
    for u in sorted(units - placed):
        if u not in dropped_txt:
            errors.append(f'unit not explained: {u} (place it in a responsibility or list it in dropped)')
    for r in resp.values():
        for u in r['units']:
            if u not in units:
                errors.append(f'responsibility {r["id"]}: unknown unit {u}')
        if not r.get('cite') or r['cite'] == ['file:line']:
            warns.append(f'responsibility {r["id"]} has no cite')
    api_ops = {k.split(' ', 1)[-1] for k in m['contracts'].get('api', {})} | set(m['contracts'].get('api', {}))
    for s in m['state']:
        if s['name'] == 'TODO':
            continue
        owners = s['owner'] if isinstance(s['owner'], list) else [s['owner']]
        if len(owners) != 1:
            errors.append(f'state {s["name"]}: {len(owners)} owners (exactly one writer)')
        o = owners[0] if owners else ''
        if o not in units and not o.startswith(('server:', 'external:', 'platform:')):
            errors.append(f'state {s["name"]}: owner {o} is not a unit (or server:/external:/platform:)')
        for r in s.get('readers', []):
            if r not in units:
                errors.append(f'state {s["name"]}: unknown reader {r}')
    for fl in m['flows']:
        for st in fl.get('steps', []):
            if fl['name'] != 'TODO' and st not in resp:
                errors.append(f'flow {fl["name"]}: unknown responsibility {st}')
    # cites must point at real code
    cites = re.findall(r'"([\w./@-]+\.(?:tsx?|jsx?|swift|kt)):(\d+)"', json.dumps({k: m[k] for k in ('concepts', 'state', 'responsibilities', 'flows', 'dropped')}))
    for f, line in cites:
        p = os.path.join(repo, f)
        if not os.path.exists(p):
            errors.append(f'cite to missing file: {f}:{line}')
        elif int(line) > sum(1 for _ in open(p, errors='ignore')):
            errors.append(f'cite past end of file: {f}:{line}')
    # every contract must be accounted for somewhere in the model's interpretation
    interp = json.dumps({k: m[k] for k in ('state', 'responsibilities', 'flows', 'dropped', 'concepts')})
    lost = [f'{kind}: {v}' for kind, items in m['contracts'].items() for v in items
            if v.split(' ', 1)[-1].split(' -> ')[0] not in interp and v not in interp]
    for c in lost:
        errors.append(f'contract not accounted for: {c}')
    filled = all(x != 'TODO' for x in [m['concepts'][0]['name'] if m['concepts'] else 'x', next(iter(resp), 'TODO')])
    print(f'featuremodel check {m["feature"]}: {len(units)} units, {len(resp)} responsibilities, {len(m["state"])} state, '
          f'{len(m["flows"])} flows, {len(cites)} cites -> {"PASS" if not errors and filled else "FAIL"}')
    for e in errors[:40]:
        print('  ERROR ' + e)
    for w in warns[:15]:
        print('  warn  ' + w)
    sys.exit(0 if not errors and filled else 1)


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    {'scaffold': scaffold, 'check': check}[a[0]](a, opt)


if __name__ == '__main__':
    main()
