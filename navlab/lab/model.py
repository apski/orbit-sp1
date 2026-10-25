#!/usr/bin/env python3
"""Fit and use the per-push cost model from sweep data.

  model.py fit [traces/sim]        -> <root>/model.json (+ leave-one-sweep-out validation printout)
  model.py fit-labs [traces/sim]   lists + startup labs -> <root>/model_lists.json, model_startup.json
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
        # probe-ruler arms (rn_navaction*) have JS only and a leave-one-depth-out error (gap g0037)
        r = pred(a['coef']['render'], f) + (a.get('content_delta_render_ms', {}).get(f'{screen}:{rows}', 0) if screen else 0) if 'render' in a['coef'] else None
        mae = a.get('mae_leave_one_sweep_out') or a.get('mae_leave_one_depth_out') or {}
        res[label] = {'js': pred(a['coef']['js'], f), 'render': r,
                      'err': max((v.get('render', v.get('js', 0)) if isinstance(v, dict) else v) for v in mae.values()) if mae else None}
    print(f'path {path}  (navigators={depth}, tab routes on path={tabs}, leaf stack={stack_len}'
          + (f', screen {screen} x{rows}' if screen else '') + f')   model: {model_path}')
    for label, v in res.items():
        print(f'  {label:7} JS/push ~{v["js"]:.0f}ms   render ~{v["render"]:.0f}ms (worst held-out error ±{v["err"]}ms)')
    ref = 'native_frames' if 'native_frames' in res else 'native'
    if 'rn' in res and ref in res:
        d = res['rn']['render'] - res[ref]['render']
        print(f'  RN - {ref}: {d:+.0f}ms per push = {d / 16.7:+.1f} frames at 60Hz ({d / 8.3:+.1f} at 120Hz)')


# ---- lists and startup labs (model.py fit-labs [root] -> <root>/model_lists.json, model_startup.json)
# Per-config medians (grouped by trace slug) from every lists/startup sweep dir, fitted per arm and list kind
# with the same least squares, validated leave-one-sweep-out. The nav model.json is not touched.
ROW_VIEWS = {'simple': 5, 'card': 11, 'heavy': 25}          # drawing views per row (layout-only views flattened)
ROW_H = {'simple': 72, 'card': 320, 'heavy': 420}
LIST_SWEEPS = ['listaa', 'lists', 'listcount', 'listsv', 'listimg', 'listwin', 'listdraw', 'listspeed', 'listfixed']
START_SWEEPS = ['startaa', 'startup', 'startmods', 'startbundle', 'startnatives']


def row_shape(item):
    """(views per row, mean row height) for an item knob; mixed cycles simple/card/heavy."""
    ks = ['simple', 'card', 'heavy'] if item == 'mixed' else [item]
    return st.mean(ROW_VIEWS[k] for k in ks), st.mean(ROW_H[k] for k in ks)


def lab_points(root, sweeps, metrics):
    """[(sweep, arm dir suffix, config dict, {metric: median})] per config slug."""
    out = []
    for d in sorted(os.listdir(root)):
        sw = next((s for s in sweeps if d == s or d.startswith(s + '-')), None)
        path = os.path.join(root, d, 'runs.csv')
        if not sw or not os.path.exists(path):
            continue
        g = defaultdict(list)
        for r in csv.DictReader(open(path)):
            if r.get('complete') == 'True':
                g[r['trace'].rsplit('_r', 1)[0]].append(r)
        for rs in g.values():
            vals = {}
            for m in metrics:
                xs = [float(r[m]) for r in rs if r.get(m) not in (None, '')]
                vals[m] = st.median(xs) if xs else None
            out.append((sw, d.removeprefix(sw), rs[0], vals))
    return out


def fit_loso(pts, names):
    """pts: [(sweep, features, y)] -> coef dict, in-sample MAE, leave-one-sweep-out MAE per sweep."""
    X, y = [p[1] for p in pts], [p[2] for p in pts]
    c = lstsq(X, y)
    mae = round(st.mean(abs(sum(a * b for a, b in zip(c, p[1])) - p[2]) for p in pts), 1)
    loso = {}
    for sw in sorted({p[0] for p in pts}):
        tr, te = [p for p in pts if p[0] != sw], [p for p in pts if p[0] == sw]
        if len(tr) >= len(names) + 2 and len({tuple(p[1]) for p in tr}) > len(names):
            cc = lstsq([p[1] for p in tr], [p[2] for p in tr])
            loso[sw] = round(st.mean(abs(sum(a * b for a, b in zip(cc, p[1])) - p[2]) for p in te), 1)
    return {'coef': dict(zip(names, (round(v, 4) for v in c))), 'points': len(pts), 'mae_in_sample': mae, 'mae_leave_one_sweep_out': loso}


def fit_lists(root):
    """Per list kind: first mount ~ views mounted initially; scroll JS/main CPU per second ~ views scrolled
    per second; memory ~ views kept mounted. Fitted only where a kind has enough configs."""
    pts = lab_points(root, LIST_SWEEPS, ['listmount_first', 'rows_initial', 'rows_mounted', 'scroll_js_cpu_s',
                                         'scroll_main_cpu_s', 'hitch_ratio', 'blank_pct', 'mb_scrolled'])
    model = {'root': root, 'row_views': ROW_VIEWS, 'row_height': ROW_H, 'kinds': {}}
    for kind in sorted({p[2].get('list') for p in pts}):
        kp = [p for p in pts if p[2].get('list') == kind]
        fits = {}
        mount = []
        for sw, _, c, v in kp:
            views, h = row_shape(c['item'])
            if v['listmount_first'] is not None and v['rows_initial'] is not None:
                mount.append((sw, [1.0, v['rows_initial'] * views], v['listmount_first']))
        if len({p[1][1] for p in mount}) >= 3:
            fits['mount_ms'] = {**fit_loso(mount, ['base', 'per_view_mounted']), 'x': 'rows mounted at first paint x views/row'}
        for m in ('scroll_js_cpu_s', 'scroll_main_cpu_s', 'hitch_ratio'):
            sp = []
            for sw, _, c, v in kp:
                views, h = row_shape(c['item'])
                if v[m] is not None:
                    sp.append((sw, [1.0, float(c['speed']) / h * views], v[m]))
            if len({p[1][1] for p in sp}) >= 3:
                fits[m] = {**fit_loso(sp, ['base', 'per_view_per_s']), 'x': 'views scrolled into view per second (speed / row height x views/row)'}
        mem = [(sw, [1.0, v['rows_mounted'] * row_shape(c['item'])[0]], v['mb_scrolled']) for sw, _, c, v in kp
               if v['rows_mounted'] is not None and v['mb_scrolled'] is not None]
        if len({p[1][1] for p in mem}) >= 3:
            fits['mb'] = {**fit_loso(mem, ['base', 'per_view_mounted']), 'x': 'rows kept mounted after scrolling x views/row'}
        model['kinds'][kind] = fits
    return model


def fit_startup(root):
    """RN: startup = a + b*modules executed + c*modules bundled (k) + d*native modules + e*tile rows.
    Native: a + e*tile rows. Phase slopes come from the same points."""
    pts = lab_points(root, START_SWEEPS, ['startup_ms', 'd_native_jsstart', 'd_jsstart_modules', 'd_modules_render',
                                           'd_render_ready', 'ph_native', 'mb_ready'])
    model = {'root': root, 'arms': {}}
    for arm in ('rn', 'native'):
        ap = [p for p in pts if (p[2].get('arm') or 'rn') == arm]
        if not ap:
            continue
        f = lambda c: [1.0, float(c.get('modules') or 0) / 1000, float(c.get('bundled') or 0) / 1000,
                       float(c.get('natives') or 0), float(c.get('rows') or 0)]
        names = ['base', 'per_1k_modules_executed', 'per_1k_modules_bundled', 'per_native_module', 'per_tile_row']
        if arm == 'native':
            f = lambda c: [1.0, float(c.get('rows') or 0)]
            names = ['base', 'per_tile_row']
        fits = {}
        for m in ('startup_ms', 'd_native_jsstart', 'd_jsstart_modules', 'd_render_ready', 'ph_native', 'mb_ready'):
            mp = [(sw, f(c), v[m]) for sw, _, c, v in ap if v[m] is not None]
            if len({tuple(p[1]) for p in mp}) > len(names):
                fits[m] = fit_loso(mp, names)
        model['arms'][arm] = fits
    return model


def fit_labs(root):
    for name, m in (('model_lists.json', fit_lists(root)), ('model_startup.json', fit_startup(root))):
        out = os.path.join(root, name)
        json.dump(m, open(out, 'w'), indent=1)
        print(f'\n-> {out}')
        groups = m['kinds'] if 'kinds' in m else m['arms']
        for k, fits in groups.items():
            for metric, ft in fits.items():
                terms = ' + '.join(f'{v:g}*{n}' if n != 'base' else f'{v:g}' for n, v in ft['coef'].items())
                print(f'  {k:10} {metric:18} = {terms}   (n={ft["points"]}, MAE {ft["mae_in_sample"]}, held-out {ft["mae_leave_one_sweep_out"]})')


def main():
    a = sys.argv[1:]
    if a and a[0] == 'fit-labs':
        return fit_labs(a[1] if len(a) > 1 else ROOT)
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    if a and a[0] == 'fit':
        return fit(a[1] if len(a) > 1 and not a[1].startswith('--') else ROOT)
    if a and a[0] == 'selftest':  # pins g0037: predict runs on every arm of the shipped baseline
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            predict(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'baseline', 'sim-iphone17pro-ios26.4', 'model.json'), 'tabs:4,stack,stack', 3, None, 0)
        assert all(f'  {k} ' in out.getvalue() for k in ('rn', 'native_frames', 'rn_navaction', 'rn_navaction_pop')), out.getvalue()
        print('selftest ok'); return
    if a and a[0] == 'predict':
        return predict(opt('--model', os.path.join(ROOT, 'model.json')), opt('--path', 'tabs:4,stack,stack'),
                       int(opt('--stack', '1')), opt('--screen', None), opt('--rows', '40'))
    print(__doc__)


if __name__ == '__main__':
    main()
