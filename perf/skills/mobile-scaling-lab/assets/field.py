#!/usr/bin/env python3
"""field: production truth for the lab's predictions. Normalize field perf exports, then report percentiles per screen.

  field.py ingest <export> --source sentry|posthog|metrickit|csv [--json out.json]
  field.py report <normalized.json> [--screen X] [--metric m] [--version v] [--device-class c] [--min-n 1]
                  [--before v1 --after v2]                      before/after a release, per screen x metric
                  [--stat p75 --measure]                        one number on the last line, for `ledger.py add --measure`
  field.py selftest                                             ingest + report every fixture in arch/fixtures/field

Record: {screen, metric, value_ms, device, device_class, os, app_version, ts, count}. `count` > 1 only for histogram
buckets (MetricKit), valued at the bucket midpoint. Metrics: ttid ttfd screen_load transition app_start_cold app_start_warm
hang hitch_ratio. hitch_ratio is in ms of hitch per s of scrolling (MetricKit's unit), not ms. App-wide metrics use screen
"(app)". Stdlib only.
"""
import csv, json, os, re, sys
from datetime import datetime, timezone

METRICS = ['ttid', 'ttfd', 'screen_load', 'transition', 'app_start_cold', 'app_start_warm', 'hang', 'hitch_ratio']
STATS = {'p50': 50, 'p75': 75, 'p90': 90, 'p99': 99}
# Sentry: span op / measurement -> metric (React Native SDK: React Navigation integration, TTID/TTFD, app start)
SENTRY_MEAS = {'time_to_initial_display': 'ttid', 'time_to_full_display': 'ttfd', 'app_start_cold': 'app_start_cold',
               'app_start_warm': 'app_start_warm'}
SENTRY_SPAN = {'navigation.processing': 'transition', 'ui.load.initial_display': 'ttid', 'ui.load.full_display': 'ttfd',
               'app.start.cold': 'app_start_cold', 'app.start.warm': 'app_start_warm'}
APP = '(app)'


def rec(screen, metric, value, device=None, os_=None, version=None, ts=None, device_class=None, count=1):
    return {'screen': APP if metric.startswith('app_start') or not screen else screen, 'metric': metric, 'value_ms': round(float(value), 2), 'device': device,
            'device_class': device_class or device_class_of(device), 'os': os_, 'app_version': version, 'ts': ts, 'count': count}


def device_class_of(model):
    """ponytail: iPhone model-id generation only (iPhone15,x+ high, 13-14 mid, older low); Android -> unknown. Prefer the
    SDK's own class (Sentry device.class, or a device_class property you send) when present."""
    m = re.match(r'iPhone(\d+),', model or '')
    if not m:
        return 'unknown'
    g = int(m.group(1))
    return 'high' if g >= 15 else 'medium' if g >= 13 else 'low'


def version_of(release):
    """'com.acme.app@2.4.0+412' -> '2.4.0'"""
    return re.sub(r'\+.*$', '', str(release).split('@')[-1]) if release else None


def num(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def iso(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')
    return ts


def load_rows(path):
    """JSON (list | {data:[]} | {results:[]} | {results: rows, columns}) or CSV -> list of dicts."""
    if path.endswith('.csv'):
        return list(csv.DictReader(open(path, newline='', encoding='utf-8-sig')))
    d = json.load(open(path))
    if isinstance(d, dict):
        if isinstance(d.get('results'), list) and d.get('columns'):  # PostHog HogQL / query API
            return [dict(zip(d['columns'], r)) for r in d['results']]
        d = d.get('data') or d.get('results') or d.get('events') or [d]
    return d


# ---------------------------------------------------------------- sources
def ingest_sentry(path):
    out = []
    for e in load_rows(path):
        if 'contexts' in e or 'spans' in e or isinstance(e.get('measurements'), dict):  # raw transaction event
            ctx = e.get('contexts', {})
            tags = dict(e['tags']) if isinstance(e.get('tags'), list) else e.get('tags') or {}
            screen, device = e.get('transaction'), ctx.get('device', {}).get('model')
            cls = tags.get('device.class') or ctx.get('device', {}).get('class')
            os_ = ' '.join(x for x in (ctx.get('os', {}).get('name'), ctx.get('os', {}).get('version')) if x) or None
            ver, ts = version_of(e.get('release')), iso(e.get('start_timestamp') or e.get('timestamp'))
            vals = {SENTRY_MEAS[k]: num(v.get('value') if isinstance(v, dict) else v)
                    for k, v in (e.get('measurements') or {}).items() if k in SENTRY_MEAS}
            if (ctx.get('trace', {}).get('op') or '').startswith('ui.load') and e.get('start_timestamp'):
                vals.setdefault('screen_load', (e['timestamp'] - e['start_timestamp']) * 1000)
            for s in e.get('spans', []):  # a measurement wins over the span it was derived from (no double count)
                if s.get('op') in SENTRY_SPAN and s.get('timestamp'):
                    vals.setdefault(SENTRY_SPAN[s['op']], (s['timestamp'] - s['start_timestamp']) * 1000)
        else:  # Discover / Explore export row (flat keys)
            g = lambda *ks: next((e[k] for k in ks if e.get(k) not in (None, '')), None)
            op = g('transaction.op', 'span.op') or ''
            if any(k.startswith(('p50(', 'p75(', 'p95(', 'avg(', 'count(')) for k in e):
                sys.exit('sentry: aggregated export (p75(...) columns); export row-level transactions/spans instead')
            screen = g('transaction')
            device, cls = g('device.model', 'device'), g('device.class')
            os_ = ' '.join(x for x in (g('os.name'), g('os.version')) if x) or None
            ver, ts = version_of(g('release')), g('timestamp')
            vals = {m: num(e.get('measurements.' + k)) for k, m in SENTRY_MEAS.items() if num(e.get('measurements.' + k)) is not None}
            if op in SENTRY_SPAN and num(g('span.duration')) is not None:
                vals.setdefault(SENTRY_SPAN[op], num(g('span.duration')))
            if op.startswith('ui.load') and num(g('transaction.duration')) is not None:
                vals.setdefault('screen_load', num(g('transaction.duration')))
            # 'navigation' transaction.duration is NOT a transition: it runs until the idle timeout. Not mapped.
        out += [rec(screen, m, v, device, os_, ver, ts, cls) for m, v in vals.items() if v is not None]
    return out


def ingest_posthog(path):
    out = []
    for e in load_rows(path):
        p = e.get('properties') or {}
        if isinstance(p, str):
            p = json.loads(p)
        screen = p.get('$screen_name') or p.get('screen') or p.get('screen_name')
        args = (p.get('$device_model') or p.get('$device_type'), ' '.join(x for x in (p.get('$os'), p.get('$os_version')) if x) or None,
                p.get('$app_version') or p.get('app_version'), e.get('timestamp'), p.get('device_class'))
        if p.get('metric') in METRICS and num(p.get('value_ms', p.get('value'))) is not None:  # custom perf event
            out.append(rec(screen, p['metric'], num(p.get('value_ms', p.get('value'))), *args))
            continue
        for m in METRICS:  # $screen (or any event) carrying <metric>_ms properties
            if (v := num(p.get(m + '_ms'))) is not None:
                out.append(rec(screen, m, v, *args))
        # ponytail: a bare $screen `duration` is usually dwell time, not load; deliberately not mapped. Send ttid_ms etc.
    return out


def ms(s):
    """'1.2 sec' / '200 ms' / '1,200 ms' -> ms"""
    m = re.match(r'\s*([\d.,]+)\s*(ms|s|sec|min)?', str(s))
    v = float(m.group(1).replace(',', ''))
    return v * {'s': 1000, 'sec': 1000, 'min': 60000}.get(m.group(2), 1)


def buckets(h, metric, screen, meta):
    """histogram -> points spread evenly inside each bucket (linear interpolation, so percentiles don't snap to midpoints);
    at most 200 points per bucket, each weighted count/points."""
    out = []
    for b in (h or {}).get('histogramValue', {}).values():
        c, lo, hi = int(b.get('bucketCount', 0)), ms(b['bucketStart']), ms(b['bucketEnd'])
        k = min(c, 200)
        out += [rec(screen, metric, lo + (i + 0.5) / k * (hi - lo), *meta, count=c / k) for i in range(k)]
    return out


def ingest_metrickit(path):
    d = json.load(open(path))
    out = []
    for p in d if isinstance(d, list) else [d]:
        md = p.get('metaData') or {}
        meta = (md.get('deviceType'), md.get('osVersion'), p.get('appVersion') or p.get('latestApplicationVersion') or md.get('appVersion'),
                p.get('timeStampBegin'))
        lm = p.get('applicationLaunchMetrics') or {}
        out += buckets(lm.get('histogrammedTimeToFirstDrawKey'), 'app_start_cold', APP, meta)  # launch to first draw
        out += buckets(lm.get('histogrammedResumeTime'), 'app_start_warm', APP, meta)          # resume from background
        out += buckets((p.get('applicationResponsivenessMetrics') or {}).get('histogrammedAppHangTime'), 'hang', APP, meta)
        if r := (p.get('animationMetrics') or {}).get('scrollHitchTimeRatio'):
            out.append(rec(APP, 'hitch_ratio', ms(r), *meta))
        for s in p.get('signpostMetrics') or []:  # mxSignpost intervals: name = screen, category = metric
            if s.get('signpostCategory') in METRICS:
                out += buckets((s.get('signpostIntervalData') or {}).get('histogrammedSignpostDurations'), s['signpostCategory'], s['signpostName'], meta)
        for h in p.get('hangDiagnostics') or []:  # MXDiagnosticPayload
            dm = h.get('diagnosticMetaData') or {}
            out.append(rec(APP, 'hang', ms(dm.get('hangDuration', 0)), dm.get('deviceType'), dm.get('osVersion'), dm.get('appVersion'), p.get('timeStampBegin')))
    return out


ALIASES = {'screen': ['screen', 'screen_name', 'route', 'view', 'transaction'], 'metric': ['metric', 'name', 'measure'],
           'value_ms': ['value_ms', 'duration_ms', 'value', 'ms', 'duration'], 'device': ['device', 'device_model', 'model'],
           'device_class': ['device_class', 'tier'], 'os': ['os', 'os_version', 'platform'],
           'app_version': ['app_version', 'version', 'release'], 'ts': ['ts', 'timestamp', 'time', 'date'], 'count': ['count', 'n']}


def ingest_csv(path):
    out = []
    for r in load_rows(path):
        g = lambda k: next((r[a] for a in ALIASES[k] if r.get(a) not in (None, '')), None)
        if g('metric') not in METRICS or num(g('value_ms')) is None:
            continue
        out.append(rec(g('screen'), g('metric'), num(g('value_ms')), g('device'), g('os'), version_of(g('app_version')), g('ts'),
                       g('device_class'), int(num(g('count')) or 1)))
    return out


SOURCES = {'sentry': ingest_sentry, 'posthog': ingest_posthog, 'metrickit': ingest_metrickit, 'csv': ingest_csv}


# ---------------------------------------------------------------- report
def pct(rs, p):
    """weighted nearest-rank percentile over records (count = weight)."""
    rs = sorted(rs, key=lambda r: r['value_ms'])
    acc, need = 0, p / 100 * sum(r['count'] for r in rs)
    for r in rs:
        acc += r['count']
        if acc >= need:
            return r['value_ms']
    return rs[-1]['value_ms']


def summary(rs):
    return {'n': sum(r['count'] for r in rs), **{k: pct(rs, p) for k, p in STATS.items()}}


def group(rs, *keys):
    g = {}
    for r in rs:
        g.setdefault(tuple(r[k] or '?' for k in keys), []).append(r)
    return g


def report(a, opt):
    d = json.load(open(a[1]))
    rs = d['records'] if isinstance(d, dict) else d
    for k, f in (('--screen', 'screen'), ('--metric', 'metric'), ('--version', 'app_version'), ('--device-class', 'device_class')):
        if opt(k):
            rs = [r for r in rs if str(r[f]) == opt(k)]
    min_n = int(opt('--min-n', 20 if '--measure' in a else 1))
    if '--measure' in a:
        n = sum(r['count'] for r in rs)
        if n < min_n:
            sys.exit(f'field: n={n} < --min-n {min_n} for this filter; not enough field data to judge')
        print(f"n={n:g}")
        print(pct(rs, STATS[opt('--stat', 'p75')]))
        return
    if not rs:
        sys.exit('field: no records match')
    unit = lambda m: 'ms/s' if m == 'hitch_ratio' else 'ms'
    print(f"{'screen':<22} {'metric':<15} {'version':<9} {'class':<8} {'n':>6} {'p50':>8} {'p75':>8} {'p90':>8} {'p99':>8}")
    for (scr, m), g in sorted(group(rs, 'screen', 'metric').items()):
        rows = [('all', 'all', g)] + [(v, 'all', x) for (v,), x in sorted(group(g, 'app_version').items())] \
            + [('all', c, x) for (c,), x in sorted(group(g, 'device_class').items())]
        for v, c, x in rows:
            s = summary(x)
            if s['n'] >= min_n:
                print(f"{scr[:22]:<22} {m:<15} {v:<9} {c:<8} {s['n']:>6g} " + ' '.join(f"{s[k]:>8.1f}" for k in STATS) + f" {unit(m)}")
    if opt('--before') and opt('--after'):
        b, af = opt('--before'), opt('--after')
        print(f"\nrelease delta {b} -> {af} (negative = faster)")
        for (scr, m), g in sorted(group(rs, 'screen', 'metric').items()):
            x, y = [r for r in g if r['app_version'] == b], [r for r in g if r['app_version'] == af]
            if x and y:
                sx, sy = summary(x), summary(y)
                f_ = lambda v: f'{v:.0f}' if v >= 100 else f'{v:.1f}'
                print(f"  {scr[:22]:<22} {m:<15} p50 {f_(sx['p50'])} -> {f_(sy['p50'])} ({(sy['p50'] / sx['p50'] - 1) * 100:+.0f}%)  "
                      f"p75 {f_(sx['p75'])} -> {f_(sy['p75'])} ({(sy['p75'] / sx['p75'] - 1) * 100:+.0f}%)  n {sx['n']:g} -> {sy['n']:g}"
                      + ('  (n<30: noisy)' if min(sx['n'], sy['n']) < 30 else ''))


def selftest():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'field')
    cases = {'sentry-transactions.json': ('sentry', {'ttid', 'ttfd', 'transition', 'app_start_cold', 'app_start_warm'}),
             'sentry-discover.csv': ('sentry', {'ttid', 'ttfd', 'screen_load', 'app_start_cold'}),
             'posthog-events.json': ('posthog', {'ttid', 'transition'}),
             'metrickit-metrics.json': ('metrickit', {'app_start_cold', 'app_start_warm', 'hang', 'hitch_ratio', 'transition'}),
             'metrickit-diagnostics.json': ('metrickit', {'hang'}),
             'generic.csv': ('csv', {'transition'})}
    for f, (src, want) in cases.items():
        rs = SOURCES[src](os.path.join(here, f))
        got = {r['metric'] for r in rs}
        assert got == want, (f, got, want)
        assert all(r['value_ms'] >= 0 and r['app_version'] for r in rs), f
        print(f"ok  {f:<28} {src:<9} {len(rs):>4} records ({sum(r['count'] for r in rs):g} samples)  metrics: {', '.join(sorted(got))}")
    t = [rec('A', 'ttid', v) for v in (10, 20, 30, 40)]
    assert (pct(t, 50), pct(t, 75), pct(t, 99)) == (20, 30, 40)
    assert pct([rec('A', 'hang', 375, count=3), rec('A', 'hang', 750, count=1)], 75) == 375  # weighted
    h = buckets({'histogramValue': {'0': {'bucketStart': '0 ms', 'bucketEnd': '100 ms', 'bucketCount': 4}}}, 'hang', APP, (None,) * 4)
    assert [r['value_ms'] for r in h] == [12.5, 37.5, 62.5, 87.5] and rec('Home', 'app_start_cold', 1)['screen'] == APP
    assert ms('2.5 sec') == 2500 and ms('1,200 ms') == 1200 and ms('1.8 ms per s') == 1.8
    assert version_of('com.acme.card@2.4.0+412') == '2.4.0' and device_class_of('iPhone12,8') == 'low'
    print('selftest ok (synthetic fixtures only)')


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    cmd = a[0] if a else 'help'
    if cmd == 'ingest':
        src = opt('--source')
        if src not in SOURCES:
            sys.exit(f'--source must be one of {", ".join(SOURCES)}')
        rs = SOURCES[src](a[1])
        out = {'source': src, 'export': os.path.abspath(a[1]), 'ingested': datetime.now(timezone.utc).isoformat(), 'records': rs}
        by = {}
        for r in rs:
            by[r['metric']] = by.get(r['metric'], 0) + r['count']
        print(f"{src}: {len(rs)} records ({sum(by.values()):g} samples) from {os.path.basename(a[1])}: "
              + ', '.join(f'{m} {n:g}' for m, n in sorted(by.items())))
        print(f"  screens: {', '.join(sorted({r['screen'] for r in rs}))}   versions: {', '.join(sorted({str(r['app_version']) for r in rs}))}")
        if opt('--json'):
            json.dump(out, open(opt('--json'), 'w'), indent=None)
            print(f"-> {opt('--json')}")
    elif cmd == 'report':
        report(a, opt)
    elif cmd == 'selftest':
        selftest()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
