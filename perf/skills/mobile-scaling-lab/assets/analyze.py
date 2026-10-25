#!/usr/bin/env python3
"""Analyze NavLab traces: lab signposts + Time Profiler samples -> per-interval and per-run metrics.

  analyze.py traces/<sweep>/            all *.trace under it -> runs.csv, intervals.csv, summary table
  analyze.py one.trace --top 15         plus the hottest stacks inside the slowest pushes

Per interval (descend/push/render/pop): wall ms, CPU ms on the JS thread / main thread / other,
main+JS CPU by bucket (hermes, yoga, fabric-mount, uikit, ca-commit, ...), hitches, and the app's
k=/renders=/mounted=/mb= fields. Exports are cached next to each trace (*.sp.xml, *.tp.xml).
"""
import csv, os, re, statistics as st, subprocess, sys, xml.etree.ElementTree as ET
from collections import Counter, defaultdict

SP_XPATH = '/trace-toc/run/data/table[@schema="os-signpost" and @category="PointsOfInterest"]'
TP_XPATH = '/trace-toc/run/data/table[@schema="time-profile"]'

# Stack -> bucket: the first rule (in this order) matching ANY frame of the stack wins, so work is charged
# to the subsystem that caused it (UIView init under RCTMountingManager is fabric-mount, not uikit).
BUCKETS = [
    ('hermes-gc', re.compile(r'HadesGC|hermes::vm::GC|GCBase')),
    ('yoga', re.compile(r'facebook::yoga|YGNode|yoga::')),
    ('text-layout', re.compile(r'TextLayoutManager|CoreText|TextMeasure|NSAttributedString|CTLine|CTFrame|NSTextLayout|NSStringDrawing')),
    ('fabric-mount', re.compile(r'RCTMountingManager|RCTComponentViewRegistry|mountChildComponentView|RCTViewComponentView|updateProps|RCTMountingTransactionObserv')),
    ('rn-screens', re.compile(r'\bRNS|RNScreens|react_native_screens')),
    ('fabric-commit', re.compile(r'ShadowTree|ShadowNode|Differentiator|calculateShadowViewMutations|LayoutableShadowNode|UIManager::')),
    ('hermes', re.compile(r'hermes::|libhermes|HermesRuntime|_sh_')),
    ('app-native', re.compile(r'NavLabNative|WireframeVC|FeedCell|BarScreen|DetailVC|HubVC|HomeVC|LazyLevel')),  # UIKit twin's screen code
    ('autolayout', re.compile(r'NSISEngine|^expression_|NSLayoutConstraint|_UILayoutEngine|updateConstraints|_engine|NSContentSizeLayoutConstraint')),
    ('uikit-nav', re.compile(r'UINavigationController|UINavigationBar|UITabBarController|UITabBar|transitionConductor|_UIViewControllerTransition')),
    ('uikit-layout', re.compile(r'layoutSubviews|layoutSublayersOfLayer|sizeThatFits|systemLayoutSizeFitting')),
    ('uikit', re.compile(r'^-\[UI|^_UI|^__.*-\[UI|UIKitCore')),
    ('ca-commit', re.compile(r'CA::Transaction::commit|CA::Layer::layout_and_display|CA::Context::commit|CA::Layer::display')),
    ('dyld', re.compile(r'^dyld[34]?::|^_dyld_|^dyld_')),
]


def export(trace, xpath, suffix):
    out = trace.rstrip('/') + suffix
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(trace):
        with open(out, 'w') as f:
            subprocess.run(['xcrun', 'xctrace', 'export', '--input', trace, '--xpath', xpath], stdout=f, check=True)
    return out


class Refs:
    """xctrace XML dedupes repeated values: <x id="5" fmt=..> then <x ref="5"/>."""

    def __init__(self, root):
        self.ids = {e.attrib['id']: e for e in root.iter() if 'id' in e.attrib}

    def el(self, e):
        return self.ids[e.attrib['ref']] if e is not None and 'ref' in e.attrib else e

    def fmt(self, e):
        e = self.el(e)
        return None if e is None else e.attrib.get('fmt')

    def num(self, e):
        return int(self.el(e).text)


def kv(msg):
    return dict(p.split('=', 1) for p in (msg or '').split() if '=' in p)


def load_signposts(trace):
    root = ET.parse(export(trace, SP_XPATH, '.sp.xml')).getroot()
    r = Refs(root)
    tables = root.findall('node')[:1]  # two PointsOfInterest tables exist; they hold the same rows
    cfg, events, opens, intervals, hitches = {}, [], {}, [], []
    for row in (tables[0].iter('row') if tables else []):
        if r.fmt(row.find('subsystem')) != 'lab':
            continue
        t = r.num(row.find('event-time')) / 1e6  # ms since trace start
        kind, name = r.fmt(row.find('event-type')), r.fmt(row.find('signpost-name'))
        msg = r.fmt(row.find('os-log-metadata')) or ''
        if kind == 'Event':
            if name == 'config':
                cfg = kv(msg)
            elif name == 'hitch':
                hitches.append((t, float(kv(msg)['ms'])))
            else:
                events.append((t, name, kv(msg)))
        elif kind == 'Begin':
            opens[r.fmt(row.find('os-signpost-identifier'))] = (t, name, kv(msg))
        elif kind == 'End':
            b = opens.pop(r.fmt(row.find('os-signpost-identifier')), None)
            if b:
                intervals.append({'name': name, 't0': b[0], 't1': t, 'ms': t - b[0], **b[2], **kv(msg)})
    return cfg, events, intervals, hitches


def thread_class(name):
    if name.startswith('Main Thread'):
        return 'main'
    if 'react.runtime.JavaScript' in name or 'JavaScript' in name:
        return 'js'
    return 'other'


def load_samples(trace):
    """[(t_ms, thread_class, weight_ms, frames leaf-first)] for the app process."""
    root = ET.parse(export(trace, TP_XPATH, '.tp.xml')).getroot()
    r = Refs(root)
    out = []
    for row in root.iter('row'):
        bt = r.el(row.find('tagged-backtrace'))
        frames = [] if bt is None else [r.el(f).attrib.get('name', '') for f in bt.findall('frame')]
        out.append((r.num(row.find('sample-time')) / 1e6, thread_class(r.fmt(row.find('thread')) or ''),
                    r.num(row.find('weight')) / 1e6, frames))
    out.sort(key=lambda s: s[0])
    return out


def bucket(frames):
    for b, rx in BUCKETS:
        if any(rx.search(f) for f in frames):
            return b
    return 'other'


def link_layout(events, intervals):
    """push.layout_ms: push() -> the `layout` event for the same k (RN: JS onLayout from the shadow tree;
    native: first viewDidLayoutSubviews). render.ms is the later, native 'attached + committed' point."""
    lay, n = {}, Counter()
    for e in events:
        if e[1] == 'layout':
            lay.setdefault(e[2].get('k'), e[0])  # first layout; RN lays a pushed screen out twice
            n[e[2].get('k')] += 1
    for iv in intervals:
        if iv['name'] == 'push' and iv.get('k') in lay:
            iv['layout_ms'] = lay[iv['k']] - iv['t0']
            iv['layouts'] = n[iv['k']]


def attribute(intervals, samples, hitches):
    import bisect
    ts = [s[0] for s in samples]
    for iv in intervals:
        cpu, buckets = Counter(), Counter()
        for s in samples[bisect.bisect_left(ts, iv['t0']):bisect.bisect_right(ts, iv['t1'])]:
            cpu[s[1]] += s[2]
            if s[1] != 'other':
                buckets[bucket(s[3])] += s[2]
        iv.update(js_cpu=cpu['js'], main_cpu=cpu['main'], other_cpu=cpu['other'], buckets=dict(buckets))
        hs = [h for t, h in hitches if iv['t0'] <= t <= iv['t1']]
        iv.update(hitches=len(hs), hitch_ms=sum(hs))


def med(xs):
    return round(st.median(xs), 1) if xs else ''


def summarize(path, cfg, events, intervals):
    by = defaultdict(list)
    for iv in intervals:
        by[iv['name']].append(iv)
    push, render, pop = by['push'], by['render'], by['pop']
    ready = next((e for e in events if e[1] == 'ready'), None)
    head, tail = push[:5], push[-5:]
    row = {'trace': os.path.basename(path), **cfg,
           'startup_ms': float(ready[2]['ms']) if ready else '',
           'descend_ms': med([i['ms'] for i in by['descend']]),
           'descend_jsms': med([i['js_cpu'] for i in by['descend']]),
           'layout_ms': med([i['layout_ms'] for i in push if 'layout_ms' in i]),
           'layouts': med([i['layouts'] for i in push if 'layouts' in i]),
           'render_ms': med([i['ms'] for i in render]),
           'render_first5': med([i['ms'] for i in render[:5]]),
           'render_last5': med([i['ms'] for i in render[-5:]]),
           'push_ms': med([i['ms'] for i in push]),
           'push_first5': med([i['ms'] for i in head]),
           'push_last5': med([i['ms'] for i in tail]),
           'push_js_cpu': med([i['js_cpu'] for i in push]),
           'push_main_cpu': med([i['main_cpu'] for i in push]),
           'push_renders': med([float(i.get('renders', 0)) for i in push]),
           'push_nav': med([float(i.get('nav', 0)) for i in push]),
           'push_hitches': sum(i['hitches'] for i in push),
           'push_hitch_ms': round(sum(i['hitch_ms'] for i in push)),
           'pop_ms': med([i['ms'] for i in pop]),
           'mb_peak': max((float(i.get('mb', 0)) for i in push), default=''),
           'complete': any(e[1] == 'done' for e in events),
           'thermal_end': next((e[2].get('thermal_end') for e in events if e[1] == 'done'), '')}
    return row


def top_stacks(intervals, samples, n):
    """Hottest leaf-ish symbols (JS + main) inside the slowest 20% of render intervals."""
    import bisect
    ts = [s[0] for s in samples]
    rs = sorted([i for i in intervals if i['name'] == 'render'], key=lambda i: -i['ms'])
    rs = rs[:max(1, len(rs) // 5)]
    c = Counter()
    for iv in rs:
        for s in samples[bisect.bisect_left(ts, iv['t0']):bisect.bisect_right(ts, iv['t1'])]:
            if s[1] == 'other':
                continue
            sym = next((f for f in s[3] if not re.match(r'^(0x|objc_msgSend|_?malloc|free|__)', f)), '?')
            c[(s[1], bucket(s[3]), sym[:110])] += s[2]
    print(f'\nhottest symbols in the slowest {len(rs)} render intervals (ms, thread, bucket, symbol):')
    for (th, b, sym), ms in c.most_common(n):
        print(f'  {ms:6.0f}  {th:5} {b:13} {sym}')


def analyze(trace):
    cfg, events, intervals, hitches = load_signposts(trace)
    samples = load_samples(trace)
    attribute(intervals, samples, hitches)
    link_layout(events, intervals)
    return cfg, events, intervals, samples


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    top = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 0
    if top:
        args = [a for a in args if a != str(top)]
    target = args[0]
    traces = [target] if target.endswith('.trace') else sorted(
        os.path.join(dp, d) for dp, ds, _ in os.walk(target) for d in ds if d.endswith('.trace'))
    outdir = target if os.path.isdir(target) and not target.endswith('.trace') else os.path.dirname(target) or '.'
    runs, ivrows = [], []
    for t in traces:
        cfg, events, intervals, samples = analyze(t)
        runs.append(summarize(t, cfg, events, intervals))
        for iv in intervals:
            b = iv.pop('buckets')
            ivrows.append({'trace': os.path.basename(t), **cfg, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in iv.items()},
                           **{f'b_{k}': round(v) for k, v in b.items()}})
        if top:
            top_stacks(intervals, samples, top)
    if not target.endswith('.trace'):  # single-trace mode only prints; never clobber a sweep's CSVs
        write_csv(os.path.join(outdir, 'runs.csv'), runs)
        write_csv(os.path.join(outdir, 'intervals.csv'), ivrows)
    cols = ['trace', 'depth', 'kind', 'screens', 'freeze', 'pushes', 'startup_ms', 'descend_ms', 'layout_ms', 'render_first5',
            'render_last5', 'push_first5', 'push_last5', 'push_js_cpu', 'push_main_cpu', 'push_renders', 'push_nav',
            'push_hitches', 'pop_ms', 'mb_peak', 'complete']
    print(' '.join(f'{c[:13]:>13}' for c in cols))
    for r in runs:
        print(' '.join(f'{str(r.get(c, ""))[:13]:>13}' for c in cols))
    if not target.endswith('.trace'):
        print(f'\n{len(runs)} runs -> {outdir}/runs.csv, {outdir}/intervals.csv')


def write_csv(path, rows):
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    main()
