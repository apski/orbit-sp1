#!/usr/bin/env python3
"""Aggregate sweeps: median over reps per config, ratio vs the sweep's first config, CPU buckets.

  report.py [traces/sim]               reads <root>/<sweep>/{runs,intervals}.csv (written by analyze.py)
  report.py --json out.json             also dump the aggregates for charts
  report.py --compare traces/sim traces/device   same configs on two targets, both arms side by side
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'traces', 'sim')
AXES = ['depth', 'kind', 'screens', 'rows', 'freeze', 'pushes']
METRICS = ['startup_ms', 'descend_ms', 'layout_ms', 'render_ms', 'push_ms', 'push_js_cpu', 'push_main_cpu', 'push_nav',
           'push_hitches', 'pop_ms', 'mb_peak']


def rows(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 1) if xs else None


def key(r):
    return tuple(r.get(a, '') for a in AXES)


def sweep(name, root):
    runs = [r for r in rows(os.path.join(root, name, 'runs.csv')) if r.get('complete') == 'True']
    ivs = rows(os.path.join(root, name, 'intervals.csv'))
    groups = defaultdict(list)
    for r in runs:
        groups[key(r)].append(r)
    out = []
    for k, rs in groups.items():
        agg = dict(zip(AXES, k), reps=len(rs))
        # runs that ended above nominal thermal state (long device sweeps heat the phone)
        agg['hot'] = sum(r.get('thermal_end', '') not in ('', 'nominal') for r in rs)
        for m in METRICS:
            vals = [f(r.get(m)) for r in rs]
            agg[m] = med(vals)
            agg[m + '_range'] = [min(v for v in vals if v is not None), max(v for v in vals if v is not None)] if any(v is not None for v in vals) else None
        # where the render (dispatch -> first layout) CPU goes, mean ms per interval
        rv = [i for i in ivs if key(i) == k and i['name'] == 'render']
        b = defaultdict(float)
        for i in rv:
            for c, v in i.items():
                if c.startswith('b_') and v:
                    b[c[2:]] += float(v) / len(rv)
        agg['render_buckets'] = {c: round(v, 1) for c, v in sorted(b.items(), key=lambda x: -x[1])}
        out.append(agg)
    return out, ivs


def per_k(ivs, name='render', field='ms', bins=10):
    """Stack sweep: metric vs push index k, binned, median over reps, split by freeze."""
    by = defaultdict(list)
    for i in ivs:
        if i['name'] == name and f(i.get(field)) is not None:
            by[(i.get('freeze'), (int(i['k']) - 1) // bins)].append(float(i[field]))
    return {f'freeze={fr} k={b * bins + 1}-{b * bins + bins}': med(v) for (fr, b), v in sorted(by.items())}


def show(name, aggs):
    if not aggs:
        return
    print(f'\n## {name}')
    cols = ['depth', 'kind', 'screens', 'rows', 'freeze', 'reps', 'startup_ms', 'descend_ms', 'layout_ms', 'render_ms', 'push_js_cpu',
            'push_main_cpu', 'push_nav', 'push_hitches', 'pop_ms', 'mb_peak']
    print(' '.join(f'{c[:12]:>12}' for c in cols) + '  render x  top render buckets (ms)')
    base = {}
    for a in sorted(aggs, key=lambda a: (a['kind'], a['freeze'], int(a['depth'] or 0) if name.startswith('rows') else 0,
                                         int(a['screens'] or 0), int(a['rows'] or 0), int(a['depth'] or 0))):
        grp = (a['kind'], a['freeze']) + ((a['depth'],) if name.startswith('rows') else ())
        if name.startswith('screens'):
            grp = (a['kind'],)
        base.setdefault(grp, a['render_ms'])
        ratio = a['render_ms'] / base[grp] if a['render_ms'] and base[grp] else 0
        top = ', '.join(f'{k} {v}' for k, v in list(a['render_buckets'].items())[:4])
        print(' '.join(f'{str(a.get(c, ""))[:12]:>12}' for c in cols) + f'  {ratio:6.2f}x  {top}')


def compare(name, rn, nat):
    """Same config, both arms: RN / native ratio of the user-felt metrics."""
    nk = {key(a): a for a in nat}
    pairs = [(a, nk[key(a)]) for a in rn if key(a) in nk]
    if not pairs:
        return
    print(f'\n## {name}: RN vs native (median of reps)')
    ms = ['startup_ms', 'descend_ms', 'layout_ms', 'render_ms', 'push_ms', 'push_hitches', 'mb_peak']
    print(f'{"config":>28} ' + ' '.join(f'{m[:12]:>20}' for m in ms))
    for a, b in sorted(pairs, key=lambda p: (p[0]['kind'], p[0]['freeze'], int(p[0]['screens'] or 0), int(p[0]['rows'] or 0), int(p[0]['depth'] or 0))):
        cfg = f"d{a['depth']} {a['kind']} s{a['screens']} r{a['rows']} f{a['freeze']}"
        cells = []
        for m in ms:
            x, y = a.get(m), b.get(m)
            cells.append(f'{x}/{y}' + (f' {x / y:.1f}x' if x and y else ''))
        print(f'{cfg:>28} ' + ' '.join(f'{c:>20}' for c in cells))


def cfg_name(a):
    return f"d{a['depth']} {a['kind']} s{a['screens']} r{a['rows']} f{a['freeze']}"


def order(a):
    return (a['kind'], a['freeze'], int(a['screens'] or 0), int(a['rows'] or 0), int(a['depth'] or 0))


def compare_targets(ra, rb):
    """Two targets (e.g. sim vs device): does the RN/native gap and the knee hold on both?"""
    la, lb = os.path.basename(ra.rstrip('/')), os.path.basename(rb.rstrip('/'))
    print(f'# {la} vs {lb}   (cells: {la}/{lb}; render = push() -> views on screen; knee = RN render vs first config)')
    for base in ALL_SWEEPS:
        got = {(t, arm): {key(x): x for x in sweep(base + arm, r)[0]} for t, r in ((la, ra), (lb, rb)) for arm in ('', '-native')}
        keys = [k for k in got[(la, '')] if all(k in v for v in got.values())]
        if not keys:
            print(f'\n## {base}: no configs on both targets yet')
            continue
        print(f'\n## {base}')
        hdr = ['RN render', 'native render', 'RN/nat ' + la, 'RN/nat ' + lb, 'RN JS/push', 'RN push', 'RN MB', f'knee {la}/{lb}', 'hot']
        print(f'{"config":>26} ' + ' '.join(f'{h:>15}' for h in hdr))
        first = {}
        for k in sorted(keys, key=lambda k: order(got[(la, '')][k])):
            ra_, rb_, na, nb = got[(la, '')][k], got[(lb, '')][k], got[(la, '-native')][k], got[(lb, '-native')][k]
            grp = (ra_['kind'], ra_['freeze'], ra_['depth'] if base == 'rows' else '')
            fa, fb = first.setdefault(grp, (ra_['render_ms'], rb_['render_ms']))
            pair = lambda x, y: f'{x}/{y}'
            ratio = lambda x, y: f"{x / y:.2f}" if x and y else '-'
            cells = [pair(ra_['render_ms'], rb_['render_ms']), pair(na['render_ms'], nb['render_ms']),
                     ratio(ra_['render_ms'], na['render_ms']), ratio(rb_['render_ms'], nb['render_ms']),
                     pair(ra_['push_js_cpu'], rb_['push_js_cpu']), pair(ra_['push_ms'], rb_['push_ms']),
                     pair(ra_['mb_peak'], rb_['mb_peak']),
                     f"{ratio(ra_['render_ms'], fa)}/{ratio(rb_['render_ms'], fb)}", str(rb_['hot'] + nb['hot'])]
            print(f'{cfg_name(ra_):>26} ' + ' '.join(f'{c:>15}' for c in cells))
    for sw in ('app', 'stack'):
        for arm in ('', '-native'):
            ca, cb = per_k(sweep(sw + arm, ra)[1]), per_k(sweep(sw + arm, rb)[1])
            if ca and cb:
                print(f'\n{sw}{arm} render.ms by k ({la}/{lb}): ' + '  '.join(f'{k}: {ca.get(k)}/{cb.get(k)}' for k in ca))


ALL_SWEEPS = ['app', 'nest', 'screens', 'grid', 'rows', 'stack']


def main():
    if '--compare' in sys.argv:
        i = sys.argv.index('--compare')
        return compare_targets(sys.argv[i + 1], sys.argv[i + 2])
    root = next((a for a in sys.argv[1:] if not a.startswith('--') and not a.endswith('.json')), ROOT)
    dump = {}
    for name in [n for b in ALL_SWEEPS for n in (b, b + '-native')]:
        aggs, ivs = sweep(name, root)
        show(name, aggs)
        dump[name] = aggs
        if name.endswith('-native'):
            compare(name.removesuffix('-native'), dump[name.removesuffix('-native')], aggs)
        if name.startswith(('stack', 'app')) and ivs:
            for metric, field in [('render', 'ms'), ('push', 'js_cpu'), ('push', 'mb'), ('push', 'mounted')]:
                curve = per_k(ivs, metric, field)
                dump[f'{name}_{metric}_{field}'] = curve
                print(f'\n{metric}.{field} by k: ' + '  '.join(f'{k}: {v}' for k, v in curve.items()))
    if '--json' in sys.argv:
        out = sys.argv[sys.argv.index('--json') + 1]
        json.dump(dump, open(out, 'w'), indent=1)
        print(f'\n-> {out}')


if __name__ == '__main__':
    main()
