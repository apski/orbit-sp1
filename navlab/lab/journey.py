#!/usr/bin/env python3
"""Run a navprobe journey on any app under Instruments and compare every navigation action with the model.

  journey.py <journey> [--config <repo>/navlab.config.json] [--reps 3] [--udid X] [--limit 90]
  journey.py --analyze <dir-or-trace> [--model model.json]

The app needs navprobe attached with `journeys` (and, for exit + signposts, the Lab native module): launching
with `-navlabJourney <journey>` runs the scripted navigation, signposting each action as a `navaction`
interval carrying the live shape (navigators, tab_routes, stack_len). Per action this prints the measured JS
commit time, CPU on the JS and main threads, CPU buckets, hitches, and the model's prediction for that shape.
measured - predicted = cost the navigation structure doesn't explain (screen content, re-renders, network).
"""
import json, os, statistics as st, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze as A  # noqa: E402
import sweep as S  # noqa: E402


def predict(coef, f):
    return coef['base'] + coef['per_navigator'] * f[0] + coef['per_tab_route'] * f[1] + coef['per_stack_screen'] * f[2]


def arm_for(model, typ):
    """navprobe-ruler arm by action type (pops are cheaper than pushes); falls back to the push model."""
    a = model['arms']
    if typ in ('GO_BACK', 'POP', 'POP_TO_TOP') and 'rn_navaction_pop' in a:
        return a['rn_navaction_pop']
    return a.get('rn_navaction', a['rn'])


def analyze_dir(target, model_path):
    traces = [target] if target.endswith('.trace') else sorted(os.path.join(target, d) for d in os.listdir(target) if d.endswith('.trace'))
    model = json.load(open(model_path)) if model_path and os.path.exists(model_path) else None
    rows = []
    for t in traces:
        cfg, events, intervals, samples = A.analyze(t)
        for i, iv in enumerate(x for x in sorted(intervals, key=lambda x: x['t0']) if x['name'] == 'navaction'):
            f = [float(iv.get('navigators', 0)), float(iv.get('tab_routes', 0)), float(iv.get('stack_len', 0))]
            rows.append({'trace': os.path.basename(t), 'i': i, 'type': iv.get('type'), 'target': iv.get('target'),
                         'ms': iv['ms'], 'js_cpu': iv['js_cpu'], 'main_cpu': iv['main_cpu'], 'hitches': iv['hitches'],
                         'navigators': f[0], 'tab_routes': f[1], 'stack_len': f[2],
                         'pred_js': predict(arm_for(model, iv.get('type'))['coef']['js'], f) if model else None,
                         'buckets': iv['buckets']})
    if not rows:
        print('no navaction intervals: is navprobe attached with signposts (the Lab module)?')
        return
    if model and 'rn_navaction' not in model['arms']:
        print('WARNING: model has no rn_navaction arm (run the probecal sweep): predictions use the push ruler, so "unexplained" is not comparable')
    # median over reps per action index
    by = {}
    for r in rows:
        by.setdefault((r['i'], r['type'], r['target']), []).append(r)
    print(f'{len(traces)} trace(s), {len(by)} actions   (ms = action -> state settled; CPU from Time Profiler inside it)')
    print(f'{"#":>3} {"action":<34} {"ms":>7} {"JS cpu":>7} {"pred JS":>8} {"unexplained":>11} {"main cpu":>9} {"hitch":>6}  shape (navs/tabs/stack)  top buckets')
    for (i, typ, tgt), rs in sorted(by.items()):
        m = lambda k: st.median(r[k] for r in rs)
        pj = rs[0]['pred_js']
        b = {}
        for r in rs:
            for k, v in r['buckets'].items():
                b[k] = b.get(k, 0) + v / len(rs)
        top = ', '.join(f'{k} {v:.0f}' for k, v in sorted(b.items(), key=lambda x: -x[1])[:3])
        un = f'{m("js_cpu") - pj:+.0f}ms' if pj is not None else '-'
        print(f'{i:>3} {(typ or "") + " " + (tgt or ""):<34.34} {m("ms"):>7.1f} {m("js_cpu"):>7.0f} {pj if pj is None else round(pj):>8} {un:>11} {m("main_cpu"):>9.0f} {m("hitches"):>6.0f}  '
              f'{rs[0]["navigators"]:.0f}/{rs[0]["tab_routes"]:.0f}/{rs[0]["stack_len"]:.0f}  {top}')


def main():
    a = sys.argv[1:]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    if '--analyze' in a:
        return analyze_dir(opt('--analyze', '.'), opt('--model', None))
    name = a[0]
    cfg_path = opt('--config', 'navlab.config.json')
    cfg = json.load(open(cfg_path))
    repo = os.path.dirname(os.path.abspath(cfg_path))
    udid = opt('--udid', cfg.get('udid') or os.environ.get('UDID'))
    reps, limit = int(opt('--reps', '3')), int(opt('--limit', str(cfg.get('journeyLimit', 90))))
    out_dir = os.path.join(repo, '.navlab', 'traces', 'device' if S.is_device(udid) else 'sim', f'journey-{name}')
    os.makedirs(out_dir, exist_ok=True)
    for rep in range(1, reps + 1):
        out = os.path.join(out_dir, f'{name}_{time.strftime("%H%M%S")}_r{rep}.trace')
        S.record(out, [*cfg.get('launchArgs', ['-navprobe', '1']), '-navlabJourney', name], udid, cfg['bundleId'], limit=limit, proc=cfg['process'], lead=())
    model = cfg.get('model') and os.path.join(repo, cfg['model'])
    analyze_dir(out_dir, model)


if __name__ == '__main__':
    main()
