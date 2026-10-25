#!/usr/bin/env python3
"""Fit and use the per-push cost model from sweep data.

  model.py fit [traces/sim]        -> <root>/model.json (+ leave-one-sweep-out validation printout)
  model.py predict --model traces/sim/model.json --path tabs:4,stack,stack [--stack 12] [--screen grid --rows 160]

Per arm, per target (push JS CPU, render ms):  y = a + b*navigators + c*tab_routes + d*stack_len
  navigators  navigators on the root->leaf path (all re-render on every push)
  tab_routes  sum of routes held by the tab navigators on that path (tab state lists every tab)
  stack_len   screens on the leaf stack when pushing (native-stack walks its route list)
Fitted on rows=40, freeze off. Screen content is a separate lookup (rows sweep): median render per
(screen type, rows) minus the rows=40 median, added on top.
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'traces', 'sim')
SWEEPS = ['app', 'nest', 'screens', 'grid', 'stack']
TYPES = {0: 'feed', 1: 'form', 2: 'grid'}  # Detail k cycles form/grid/feed: TYPES[k % 3]


def kind_at(level, depth, kind):
    """Mirror of the apps' kindAt()."""
    if level == depth:
        return 'stack'
    if kind == 'mix':
        return 'tabs' if level % 2 else 'stack'
    if kind == 'tabroot':
        return 'tabs' if level == 1 else 'stack'
    return kind


def features(depth, kind, screens, k):
    tabs = sum(screens for lv in range(1, depth) if kind_at(lv, depth, kind) == 'tabs')
    return [1.0, float(depth), float(tabs), float(k)]


NAMES = ['base', 'per_navigator', 'per_tab_route', 'per_stack_screen']


def lstsq(X, y):
    """Normal equations + Gauss-Jordan with partial pivoting (stdlib only; 4 parameters)."""
    n = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(n)] + [sum(r[i] * v for r, v in zip(X, y))] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        if abs(A[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = A[r][c] / A[c][c]
                A[r] = [a - f * b for a, b in zip(A[r], A[c])]
    return [A[i][n] / A[i][i] if abs(A[i][i]) > 1e-12 else 0.0 for i in range(n)]


def points(root, arm):
    """Per (sweep, config, k-bin) medians: [(sweep, feats, js, render)]. k binned by 10 for deep stacks."""
    out = []
    for sw in SWEEPS:
        path = os.path.join(root, sw + arm, 'intervals.csv')
        if not os.path.exists(path):
            continue
        g = defaultdict(lambda: {'push': [], 'render': []})
        for r in csv.DictReader(open(path)):
            if r['name'] not in ('push', 'render') or r.get('rows', '40') != '40' or r.get('freeze') == 'true':
                continue
            k = int(r['k'])
            cfg = (int(r['depth']), r['kind'] or 'stack', int(r['screens'] or 3), (k - 1) // 10)
            g[cfg]['push' if r['name'] == 'push' else 'render'].append(
                (k, float(r['js_cpu'] or 0) if r['name'] == 'push' else float(r['ms'])))
        for (depth, kind, screens, _), v in g.items():
            if v['push'] and v['render']:
                k = st.median(x[0] for x in v['push'])
                out.append((sw, features(depth, kind, screens, k), st.median(x[1] for x in v['push']),
                            st.median(x[1] for x in v['render'])))
    return out


def fit_arm(pts):
    X = [p[1] for p in pts]
    return {'js': dict(zip(NAMES, lstsq(X, [p[2] for p in pts]))), 'render': dict(zip(NAMES, lstsq(X, [p[3] for p in pts])))}


def pred(coef, f):
    return sum(coef[n] * x for n, x in zip(NAMES, f))


def content(root, arm):
    """Render delta per (screen type, rows) vs the rows=40 median of the same type (rows sweep, depth 1)."""
    path = os.path.join(root, 'rows' + arm, 'intervals.csv')
    if not os.path.exists(path):
        return {}
    by = defaultdict(list)
    for r in csv.DictReader(open(path)):
        if r['name'] == 'render' and r['depth'] == '1':
            by[(TYPES[int(r['k']) % 3], r['rows'])].append(float(r['ms']))
    out = {}
    for (t, rows), v in by.items():
        if (t, '40') in by:
            out[f'{t}:{rows}'] = round(st.median(v) - st.median(by[(t, '40')]), 1)
    return out


def fit(root):
    model = {'root': root, 'fitted_on': 'rows=40, freeze off; per (config, k-bin) medians', 'arms': {}}
    for arm, label in (('', 'rn'), ('-native', 'native')):
        pts = points(root, arm)
        if not pts:
            continue
        m = fit_arm(pts)
        # leave-one-sweep-out: fit without a sweep, predict it, report the error on data the fit never saw
        loso = {}
        for sw in sorted({p[0] for p in pts}):
            train = [p for p in pts if p[0] != sw]
            test = [p for p in pts if p[0] == sw]
            if len(train) < 8:
                continue
            mm = fit_arm(train)
            loso[sw] = {t: round(st.mean(abs(pred(mm[t], p[1]) - p[i]) for p in test), 1)
                        for t, i in (('js', 2), ('render', 3))}
        resid = {t: round(st.mean(abs(pred(m[t], p[1]) - p[i]) for p in pts), 1) for t, i in (('js', 2), ('render', 3))}
        model['arms'][label] = {'coef': {t: {k: round(v, 3) for k, v in c.items()} for t, c in m.items()},
                                'points': len(pts), 'mae_in_sample': resid, 'mae_leave_one_sweep_out': loso,
                                'content_delta_render_ms': content(root, arm)}
    # Idiomatic native: the twin's grid with frame layout (gridfix sweep) instead of nested UIStackViews.
    # Detail screens cycle feed/form/grid, so a third of pushes land on the grid: shift the base by delta/3.
    nat = model['arms'].get('native')
    gf, rw = os.path.join(root, 'gridfix-native', 'intervals.csv'), os.path.join(root, 'rows-native', 'intervals.csv')
    if nat and os.path.exists(gf) and os.path.exists(rw):
        def grid_render(path, rows):
            return [float(r['ms']) for r in csv.DictReader(open(path)) if r['name'] == 'render' and r['rows'] == rows and TYPES[int(r['k']) % 3] == 'grid']
        frames, auto = grid_render(gf, '40'), grid_render(rw, '40')
        if frames and auto:
            shift = (st.median(auto) - st.median(frames)) / 3
            nf = json.loads(json.dumps(nat))
            nf['coef']['render']['base'] = round(nat['coef']['render']['base'] - shift, 3)
            nf['content_delta_render_ms'] = {**nat['content_delta_render_ms'], **{f'grid:{n}': round(st.median(grid_render(gf, n)) - st.median(frames), 1) for n in ('10', '160') if grid_render(gf, n)}}
            nf['note'] = f'native with frame-layout grid: base shifted by -{shift:.1f}ms (grid@40 auto {st.median(auto):.0f} vs frames {st.median(frames):.0f}, /3 for the feed/form/grid cycle)'
            model['arms']['native_frames'] = nf
            print(f'native_frames: {nf["note"]}')
    # navprobe's ruler (action -> navigation state settled) calibrated on the same configs (probecal sweep).
    # journey.py / navprobe predict in these units; the push model above covers push() -> transition end.
    pc = os.path.join(root, 'probecal', 'intervals.csv')
    for typ, arm in (('PUSH', 'rn_navaction'), ('GO_BACK', 'rn_navaction_pop')) if os.path.exists(pc) else ():
        g = defaultdict(lambda: {'js': [], 'ms': []})
        for r in csv.DictReader(open(pc)):
            if r['name'] == 'navaction' and r.get('type') == typ and r.get('navigators'):
                f = (int(r['depth']), float(r['navigators']), float(r['tab_routes']), float(r['stack_len']))
                g[f]['js'].append(float(r['js_cpu'] or 0))
                g[f]['ms'].append(float(r['ms']))
        pts = [(f[0], [1.0, f[1], f[2], f[3]], st.median(v['js']), st.median(v['ms'])) for f, v in g.items()]
        if len(pts) < 8:
            continue
        coef = {'js': dict(zip(NAMES, lstsq([p[1] for p in pts], [p[2] for p in pts]))),
                'render': dict(zip(NAMES, lstsq([p[1] for p in pts], [p[3] for p in pts])))}
        loso = {}
        for d in sorted({p[0] for p in pts}):
            tr, te = [p for p in pts if p[0] != d], [p for p in pts if p[0] == d]
            if len(tr) >= 6:
                cj = dict(zip(NAMES, lstsq([p[1] for p in tr], [p[2] for p in tr])))
                loso[f'depth{d}'] = {'js': round(st.mean(abs(pred(cj, p[1]) - p[2]) for p in te), 1)}
        model['arms'][arm] = {'coef': {t: {k: round(v, 3) for k, v in c.items()} for t, c in coef.items()}, 'points': len(pts),
                              'mae_leave_one_depth_out': loso, 'note': f'navprobe ruler for {typ} (dispatch -> state settled); render = interval ms'}
        c = coef['js']
        print(f'{arm} points={len(pts)}  js = {c["base"]:.1f} + {c["per_navigator"]:.2f}*navigators + {c["per_tab_route"]:.3f}*tab_routes'
              f' + {c["per_stack_screen"]:.2f}*stack_len   held-out by depth: {loso}')
    out = os.path.join(root, 'model.json')
    json.dump(model, open(out, 'w'), indent=1)
    for label, a in model['arms'].items():
        print(f'{label:7} points={a["points"]}')
        for t in ('js', 'render'):
            c = a['coef'][t]
            print(f'  {t:7} = {c["base"]:.1f} + {c["per_navigator"]:.2f}*navigators + {c["per_tab_route"]:.3f}*tab_routes'
                  f' + {c["per_stack_screen"]:.2f}*stack_len   (MAE {a["mae_in_sample"][t]}ms in-sample)')
        print('  held-out MAE (ms) by sweep:', ', '.join(f'{s}: js {v["js"]} / render {v["render"]}' for s, v in a['mae_leave_one_sweep_out'].items()))
    print(f'-> {out}')


def predict(model_path, path, stack_len, screen, rows):
    m = json.load(open(model_path))
    navs = path.split(',')
    depth = len(navs)
    tabs = sum(int(n.split(':')[1]) if ':' in n else 4 for n in navs[:-1] if n.startswith('tabs'))
    f = [1.0, float(depth), float(tabs), float(stack_len)]
    res = {}
    for label, a in m['arms'].items():
        r = pred(a['coef']['render'], f) + (a['content_delta_render_ms'].get(f'{screen}:{rows}', 0) if screen else 0)
        res[label] = {'js': pred(a['coef']['js'], f), 'render': r,
                      'err': a['mae_leave_one_sweep_out'] and max(v['render'] for v in a['mae_leave_one_sweep_out'].values())}
    print(f'path {path}  (navigators={depth}, tab routes on path={tabs}, leaf stack={stack_len}'
          + (f', screen {screen} x{rows}' if screen else '') + f')   model: {model_path}')
    for label, v in res.items():
        print(f'  {label:7} JS/push ~{v["js"]:.0f}ms   render ~{v["render"]:.0f}ms (worst held-out error ±{v["err"]}ms)')
    ref = 'native_frames' if 'native_frames' in res else 'native'
    if 'rn' in res and ref in res:
        d = res['rn']['render'] - res[ref]['render']
        print(f'  RN - {ref}: {d:+.0f}ms per push = {d / 16.7:+.1f} frames at 60Hz ({d / 8.3:+.1f} at 120Hz)')


def main():
    a = sys.argv[1:]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    if a and a[0] == 'fit':
        return fit(a[1] if len(a) > 1 and not a[1].startswith('--') else ROOT)
    if a and a[0] == 'predict':
        return predict(opt('--model', os.path.join(ROOT, 'model.json')), opt('--path', 'tabs:4,stack,stack'),
                       int(opt('--stack', '1')), opt('--screen', None), opt('--rows', '40'))
    print(__doc__)


if __name__ == '__main__':
    main()
