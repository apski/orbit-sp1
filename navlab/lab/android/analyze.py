#!/usr/bin/env python3
"""Analyze Android NavLab traces (Perfetto): lab trace sections + scheduler -> the iOS pipeline's CSVs.

  lab/android/analyze.py traces/android-emu/<sweep>/   all *.pftrace under it -> runs.csv, intervals.csv
  lab/android/analyze.py one.pftrace                   print only (never overwrites a sweep's CSVs)
  lab/android/analyze.py --selftest                    parse the checked-in fixture and check every column
  lab/android/analyze.py --floor traces/android-emu    A/A floor: rep-to-rep spread of the `aa` sweep, per arm

The trace sections come from LabCore.kt (see its header). Per interval: wall ms, CPU ms on the JS thread / main
thread / other threads of the app, from `sched` (exact running time, not samples), and CPU by thread bucket
(renderthread, gc, jit, native-modules, other-threads; Android has no symbolized stack sampling here). Columns and
summaries are lab/analyze.py's own, so lab/report.py and lab/model.py read Android sweeps unchanged.
Query results are cached next to each trace (*.lab.csv, *.sched.csv).
"""
import bisect, csv, io, os, re, subprocess, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from analyze import kv, link_layout, summarize, write_csv  # noqa: E402  iOS analyzer: same summaries, same columns

TP = os.path.join(HERE, 'bin', 'trace_processor')  # get.perfetto.dev wrapper; fetches the pinned binary once
Q_LAB = """select s.ts - (select start_ts from trace_bounds) as ts, s.dur, s.name from slice s
           where s.name glob 'lab:*' order by s.ts"""
Q_SCHED = """select ss.ts - (select start_ts from trace_bounds) as ts, ss.dur, t.tid, t.name as tname, p.pid
             from sched ss join thread t using(utid) join process p using(upid)
             where p.name glob 'com.lab.*' and ss.dur > 0 order by ss.ts"""


def query(trace, sql, suffix):
    out = trace + suffix
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(trace):
        qf = out + '.sql'
        open(qf, 'w').write(sql + ';\n')
        r = subprocess.run([TP, '-q', qf, trace], capture_output=True, text=True)
        os.remove(qf)
        body = r.stdout[r.stdout.find('"'):] if '"' in r.stdout else ''
        if r.returncode or not body:
            raise RuntimeError(f'trace_processor failed on {trace}: {r.stderr[-500:]}')
        open(out, 'w').write(body)
    return list(csv.DictReader(io.StringIO(open(out).read())))


def load_lab(rows):
    """lab:* slices -> (cfg, events, intervals, hitches), the shapes lab/analyze.py works with (ms since trace start)."""
    msg_b, msg_e, ev, spans = {}, {}, {}, []
    for r in rows:
        t, name = int(r['ts']) / 1e6, r['name']
        m = re.match(r'lab:(b|e) id=(\d+) ?(.*)', name)
        if m:  # begin/end messages, possibly split over several slices
            (msg_b if m[1] == 'b' else msg_e).setdefault(m[2], {}).update(kv(m[3]))
            continue
        m = re.match(r'lab:ev#(\d+) (\S+) ?(.*)', name)
        if m:
            e = ev.setdefault(m[1], (t, m[2], {}))
            e[2].update(kv(m[3]))
            continue
        m = re.match(r'lab:(\S+) id=(\d+)$', name)
        if m and int(r['dur']) >= 0:
            spans.append((m[1], m[2], t, t + int(r['dur']) / 1e6))
    cfg, events, hitches = {}, [], []
    for t, name, d in sorted(ev.values(), key=lambda e: e[0]):
        if name == 'config':
            cfg = d
        elif name == 'hitch':
            hitches.append((t, float(d['ms'])))
        else:
            events.append((t, name, d))
    intervals = [{'name': n, 't0': t0, 't1': t1, 'ms': t1 - t0, **msg_b.get(i, {}), **msg_e.get(i, {})}
                 for n, i, t0, t1 in sorted(spans, key=lambda s: s[2])]
    return cfg, events, intervals, hitches


def thread_class(tid, pid, name):
    if tid == pid:
        return 'main'
    return 'js' if re.search(r'mqt_(v_)?js', name or '') else 'other'


def thread_bucket(name):
    n = name or ''
    if n.startswith('RenderThread') or n.startswith('hwui'):
        return 'renderthread'
    if 'HeapTaskDaemon' in n or n.startswith('FinalizerDaemon'):
        return 'gc'
    if n.startswith('Jit thread'):
        return 'jit'
    if 'mqt_native' in n or 'mqt_v_native' in n:
        return 'native-modules'
    return 'other-threads'


def attribute(intervals, sched, hitches):
    """CPU running time of each app thread class inside each interval (sched slices clipped to it)."""
    runs = [(int(s['ts']) / 1e6, int(s['dur']) / 1e6, int(s['tid']), int(s['pid']), s['tname']) for s in sched]
    starts = [r[0] for r in runs]
    longest = max((r[1] for r in runs), default=0)
    for iv in intervals:
        cpu, buckets = Counter(), Counter()
        for t, d, tid, pid, tn in runs[bisect.bisect_left(starts, iv['t0'] - longest):bisect.bisect_right(starts, iv['t1'])]:
            ov = min(t + d, iv['t1']) - max(t, iv['t0'])
            if ov <= 0:
                continue
            c = thread_class(tid, pid, tn)
            cpu[c] += ov
            if c == 'other':
                buckets[thread_bucket(tn)] += ov
        iv.update(js_cpu=round(cpu['js'], 1), main_cpu=round(cpu['main'], 1), other_cpu=round(cpu['other'], 1),
                  buckets=dict(buckets))
        hs = [h for t, h in hitches if iv['t0'] <= t <= iv['t1']]
        iv.update(hitches=len(hs), hitch_ms=sum(hs))


def analyze(trace):
    cfg, events, intervals, hitches = load_lab(query(trace, Q_LAB, '.lab.csv'))
    attribute(intervals, query(trace, Q_SCHED, '.sched.csv'), hitches)
    link_layout(events, intervals)
    return cfg, events, intervals


def run(target):
    single = target.endswith('.pftrace')
    traces = [target] if single else sorted(
        os.path.join(dp, f) for dp, _, fs in os.walk(target) for f in fs if f.endswith('.pftrace'))
    runs, ivrows = [], []
    for t in traces:
        try:
            cfg, events, intervals = analyze(t)
        except RuntimeError as e:
            print(f'skip: {e}', file=sys.stderr)
            continue
        runs.append(summarize(t, cfg, events, intervals))
        for iv in intervals:
            b = iv.pop('buckets')
            ivrows.append({'trace': os.path.basename(t), **cfg, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in iv.items()},
                           **{f'b_{k}': round(v) for k, v in b.items()}})
    if not single:
        write_csv(os.path.join(target, 'runs.csv'), runs)
        write_csv(os.path.join(target, 'intervals.csv'), ivrows)
    cols = ['trace', 'depth', 'kind', 'screens', 'rows', 'freeze', 'startup_ms', 'descend_ms', 'layout_ms', 'render_ms',
            'push_ms', 'push_js_cpu', 'push_main_cpu', 'push_nav', 'push_hitches', 'pop_ms', 'mb_peak', 'complete']
    print(' '.join(f'{c[:13]:>13}' for c in cols))
    for r in runs:
        print(' '.join(f'{str(r.get(c, ""))[:13]:>13}' for c in cols))
    if not single:
        print(f'\n{len(runs)} runs -> {target}/runs.csv, {target}/intervals.csv')
    return runs, ivrows


def selftest():
    """Fixture: the two query results of a real RN run (depth 3 mix, 3 pushes; lab/android/fixtures/), so the
    test needs neither trace_processor nor a device. Regenerate: copy a run's *.lab.csv / *.sched.csv."""
    fx = os.path.join(HERE, 'fixtures', 'rn_depth3_mix_pushes3')
    read = lambda suffix: list(csv.DictReader(open(fx + suffix)))
    cfg, events, intervals, hitches = load_lab(read('.lab.csv'))
    attribute(intervals, read('.sched.csv'), hitches)
    link_layout(events, intervals)
    row = summarize(fx, cfg, events, intervals)
    names = Counter(i['name'] for i in intervals)
    assert cfg.get('depth') == '3' and cfg.get('kind') == 'mix' and cfg.get('thermal'), cfg
    assert names['push'] == 3 and names['render'] == 3 and names['pop'] == 3 and names['descend'] == 1, names
    assert row['complete'] and row['startup_ms'] and row['render_ms'] and row['push_js_cpu'] != '', row
    p = [i for i in intervals if i['name'] == 'push']
    assert all(i['k'] and i['mb'] and i['nav'] and i['ms'] > i.get('layout_ms', 0) > 0 for i in p), p
    assert all(0 < i['js_cpu'] + i['main_cpu'] <= 2 * i['ms'] for i in p), 'CPU attribution out of range'
    r = [i for i in intervals if i['name'] == 'render']
    assert all(0 < i['ms'] < j['ms'] for i, j in zip(r, p)), 'render must end before its push'
    # message split over several slices joins back
    _, _, ivs, _ = load_lab([{'ts': '0', 'dur': '5000000', 'name': 'lab:push id=9'},
                             {'ts': '1', 'dur': '0', 'name': 'lab:b id=9 k=1'},
                             {'ts': '2', 'dur': '0', 'name': 'lab:e id=9 renders=1 nav=3'},
                             {'ts': '3', 'dur': '0', 'name': 'lab:e id=9 mounted=2 mb=99.0'}])
    assert ivs[0]['ms'] == 5 and ivs[0]['mounted'] == '2' and ivs[0]['nav'] == '3', ivs
    print(f'selftest ok: {dict(names)}, render {row["render_ms"]}ms, push JS {row["push_js_cpu"]}ms')


def floor(root):
    """Same config run N times per arm: a difference smaller than this spread is noise on this host."""
    import statistics as st
    for arm in ('aa', 'aa-native'):
        path = os.path.join(root, arm, 'runs.csv')
        if not os.path.exists(path):
            continue
        rs = [r for r in csv.DictReader(open(path)) if r['complete'] == 'True']
        print(f'{arm}: {len(rs)} complete runs')
        for m in ('startup_ms', 'descend_ms', 'render_ms', 'push_ms', 'push_js_cpu', 'push_main_cpu', 'mb_peak'):
            v = [float(r[m]) for r in rs if r.get(m) not in ('', None)]
            if len(v) > 1:
                md = st.median(v)
                print(f'  {m:14} median {md:7.1f}  range {min(v):7.1f}-{max(v):7.1f}  spread {(max(v) - min(v)) / md * 100 if md else 0:5.1f}%')


if __name__ == '__main__':
    if '--floor' in sys.argv:
        floor(sys.argv[sys.argv.index('--floor') + 1])
    elif '--selftest' in sys.argv:
        selftest()
    elif len(sys.argv) > 1:
        run(sys.argv[1])
    else:
        print(__doc__)
