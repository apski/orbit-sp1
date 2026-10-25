"""PerfStack (docs/orbit/perf/PERFSTACK.md): journey -> screen -> phase attribution over trace evidence.

Reads trace files (runs, never the index), so it is an L3 analysis over evidence. Pure: same files and
config give byte-identical JSON. One trace file = one rep of one implementation ("arm").
"""
import html
import json
import random
from pathlib import Path

from . import VERSION

MILESTONES = ("tap", "first_frame", "data_ready", "interactive")
WAITING, UNINSTRUMENTED = "waiting", "main:uninstrumented"
SHOWN = MILESTONES[1:] + ("settled", "wall")
QUIET_MS = 50  # "settled": first main-thread quiet window this long at or after interactive (TTI-style)
BOOT, CI = 2000, 0.90


def _opts(cfg):
    p = cfg.d.get("perf", {}) if cfg else {}
    return {"phases": p.get("phases", ["navigation", "data", "render", "layout"]), "main": p.get("main", "main"),
            "base": p.get("base"), "labels": p.get("labels", {})}


# ---------- normalize: raw events -> one trace ----------

def parse(text, name):
    """JSONL events -> trace. Clock sync, b/e pairing, phase/thread inheritance, milestones."""
    events, meta, errors = [], {}, []
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                errors.append(f"line {i}: not JSON")
    for e in events:
        if e.get("k") == "meta":
            for k, v in e.items():
                if k not in ("k", "t"):
                    meta.setdefault(k, v)  # harness meta comes first and wins
    # Clocks: native events are host-epoch ms. Another source (src="js") must ship sync samples:
    # t0/t1 = its clock before/after a bridge call, tn = native clock inside it. NTP-style offset from
    # the sample with the smallest round trip; its half-RTT is the error bound.
    offsets, unsynced = {}, set()
    for e in sorted((e for e in events if e.get("k") == "sync"), key=lambda e: (e["t1"] - e["t0"], e["t0"])):
        offsets.setdefault(e.get("src", "js"), (e["tn"] - (e["t0"] + e["t1"]) / 2, (e["t1"] - e["t0"]) / 2))
    clock = {s: {"offset_ms": round(o, 3), "error_ms": round(err, 3)} for s, (o, err) in sorted(offsets.items())}

    def t(e, key="t"):
        src = e.get("src")
        if not src:
            return e[key]
        if src not in offsets:
            unsynced.add(src)
            return None
        return e[key] + offsets[src][0]

    opened, spans, marks, busy, counts, fixtures = {}, {}, [], [], {}, {}
    for e in events:
        k = e.get("k")
        if k in ("meta", "sync") or "t" not in e:
            continue
        ts = t(e)
        if ts is None:
            continue
        if k == "b":
            opened[e["id"]] = dict(id=e["id"], parent=e.get("p"), name=e["n"], phase=e.get("ph"), thread=e.get("th"),
                                   screen=e.get("sc"), t0=ts)
        elif k == "e":
            s = opened.pop(e["id"], None)
            if s is None:
                errors.append(f"end without begin: span {e['id']}")
            else:
                spans[s["id"]] = dict(s, t1=max(ts, s["t0"]))
        elif k == "m":
            marks.append((ts, "launch:tap" if e["n"] == "process:start" else e["n"]))
        elif k == "busy":
            busy.append((ts, ts + e["d"], e.get("th", "main")))
        elif k == "c":
            counts[e["n"]] = counts.get(e["n"], 0) + e["v"]
        elif k == "f":
            fixtures[e["n"]] = e["v"]
    end = max([s["t1"] for s in spans.values()] + [m[0] for m in marks] + [b[1] for b in busy] + [0])
    for s in sorted(opened.values(), key=lambda s: str(s["id"])):
        errors.append(f"unclosed span {s['id']} {s['name']}: closed at trace end")
        spans[s["id"]] = dict(s, t1=max(end, s["t0"]))
    errors += [f"unsynced clock: {s} events dropped (no sync samples)" for s in sorted(unsynced)]
    for s in spans.values():  # inherit phase/thread/screen down the tree; unknown parent -> root
        chain, p, seen = [], s, set()
        while p is not None and p["id"] not in seen:
            seen.add(p["id"])
            chain.append(p)
            p = spans.get(p["parent"]) if p["parent"] is not None else None
        if s["parent"] is not None and s["parent"] not in spans:
            errors.append(f"span {s['id']} {s['name']}: unknown parent {s['parent']}, treated as root")
        s["depth"] = len(chain) - 1
        for f, dflt in (("phase", "other"), ("thread", "main"), ("screen", None)):
            s["r_" + f] = next((c[f] for c in chain if c[f]), dflt)
    marks.sort()
    return {"name": name, "meta": meta, "clock": clock, "spans": sorted(spans.values(), key=lambda s: (s["t0"], str(s["id"]))),
            "marks": marks, "busy": sorted(busy), "counts": counts, "fixtures": fixtures, "errors": errors}


def _read(f):
    if f.suffix != ".zst":
        return f.read_text()
    try:
        from compression import zstd  # stdlib from Python 3.14
    except ImportError:
        raise FileNotFoundError(f"{f}: .zst traces need Python >= 3.14 (compression.zstd)") from None
    return zstd.decompress(f.read_bytes()).decode()


def load(path):
    """Every *.jsonl / *.jsonl.zst under `path` (or one file). Names are relative, so output has no home paths."""
    p = Path(path)
    files = sorted(f for g in ("*.jsonl", "*.jsonl.zst") for f in p.rglob(g)) if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"no *.jsonl traces under {path}")
    return [parse(_read(f), f.relative_to(p).as_posix() if p.is_dir() else f.name) for f in files]


# ---------- windows and attribution ----------

def screens(tr):
    """{screen: {milestone: abs ms}} from the first tap and the first of each milestone after it."""
    out = {}
    for t, n in tr["marks"]:
        sc, _, m = n.rpartition(":")
        if m not in MILESTONES or not sc:
            continue
        d = out.setdefault(sc, {})
        if m == "tap":
            d.setdefault("tap", t)
        elif "tap" in d and m not in d and t >= d["tap"]:
            d[m] = t
    return {k: v for k, v in out.items() if "tap" in v and "interactive" in v}


def attribute(tr, a, b, main="main"):
    """Exclusive buckets over [a, b]: every ms lands in exactly one bucket, so buckets sum to b - a.

    Per elementary interval, in order: the deepest main-thread span (its phase), else main busy with no
    span ("main:uninstrumented"), else the deepest background span (its phase: main was idle, waiting on
    it), else "waiting". Overlapped work is reported separately as `overlapped`, never added to wall time.
    ponytail: O(boundaries x spans); an interval tree if traces reach ~10k spans. Background work is
    assumed to be on the critical path when main is idle; true critical path needs dependency edges.
    """
    sp = [s for s in tr["spans"] if s["t1"] > a and s["t0"] < b]
    bz = [(x, y) for x, y, th in tr["busy"] if th == main and y > a and x < b]
    cuts = sorted({a, b} | {min(max(v, a), b) for s in sp for v in (s["t0"], s["t1"])} | {min(max(v, a), b) for x, y in bz for v in (x, y)})
    buckets, overlapped, self_ms, main_busy = {}, {}, {}, 0.0
    key = lambda s: (s["depth"], s["t0"], str(s["id"]))
    for x, y in zip(cuts, cuts[1:]):
        if y <= x:
            continue
        d = y - x
        act = [s for s in sp if s["t0"] <= x and s["t1"] >= y]
        on_main = [s for s in act if s["r_thread"] == main]
        busy = any(p <= x and q >= y for p, q in bz)
        if on_main:
            win = max(on_main, key=key)
        elif not busy and act:
            win = max(act, key=key)
        else:
            win = None
        bucket = win["r_phase"] if win else (UNINSTRUMENTED if busy else WAITING)
        buckets[bucket] = buckets.get(bucket, 0) + d
        if win:
            self_ms[win["id"]] = self_ms.get(win["id"], 0) + d
        for ph in {s["r_phase"] for s in act if s is not win} - {bucket}:
            overlapped[ph] = overlapped.get(ph, 0) + d
        if on_main or busy:
            main_busy += d
    return {"buckets": buckets, "overlapped": overlapped, "self": self_ms, "main_busy": main_busy}


def settled(tr, t_int, main="main"):
    """First moment at or after interactive followed by QUIET_MS of main-thread quiet. None if never observed."""
    occ = sorted([(s["t0"], s["t1"]) for s in tr["spans"] if s["r_thread"] == main] + [(x, y) for x, y, th in tr["busy"] if th == main])
    cur = t_int
    for x, y in occ:
        if y <= cur:
            continue
        if x - cur >= QUIET_MS:
            return cur
        cur = max(cur, y)
    end = max([s["t1"] for s in tr["spans"]] + [m[0] for m in tr["marks"]] + [y for _, y, _ in tr["busy"]] + [cur])
    return cur if end - cur >= QUIET_MS else None


def rep_window(tr, wins, main, tap):
    """Attribution summed over `wins` (one screen, or every screen of a journey). Time between screens
    (user think time, harness steps) is `between_ms`, never a bucket: it is not the app's time."""
    acc = {"buckets": {}, "overlapped": {}, "self": {}, "main_busy": 0.0}
    for a, b in wins:
        at = attribute(tr, a, b, main)
        for k in ("buckets", "overlapped", "self"):
            for x, v in at[k].items():
                acc[k][x] = acc[k].get(x, 0) + v
        acc["main_busy"] += at["main_busy"]
    a, b = min(w[0] for w in wins), max(w[1] for w in wins)
    w = sum(y - x for x, y in wins)
    inside = [s for s in tr["spans"] if s["t1"] > a and s["t0"] < b]
    shown = sorted((s for s in inside if s["t1"] - s["t0"] >= w * 0.005), key=lambda s: (-(s["t1"] - s["t0"]), str(s["id"])))[:300]
    return {
        "trace": tr["name"], "window_ms": r(w), "wall_ms": r(b - a), "between_ms": r(b - a - w), "main_busy_ms": r(acc["main_busy"]),
        "buckets": {k: r(v) for k, v in sorted(acc["buckets"].items())},
        "overlapped": {k: r(v) for k, v in sorted(acc["overlapped"].items())},
        "spans": [{"id": str(s["id"]), "name": s["name"], "phase": s["r_phase"], "thread": s["r_thread"], "depth": s["depth"],
                   "start_ms": r(max(s["t0"], a) - tap), "end_ms": r(min(s["t1"], b) - tap), "self_ms": r(acc["self"].get(s["id"], 0))}
                  for s in sorted(shown, key=lambda s: (s["t0"], str(s["id"])))],
        "spans_dropped": len(inside) - len(shown),
        "busy": [[r(max(x, a) - tap), r(min(y, b) - tap)] for x, y, th in tr["busy"] if th == main and y > a and x < b],
    }


# ---------- stats ----------

def r(x):
    return round(x + 0.0, 1)


def pct(v, q):  # nearest-rank
    s = sorted(v)
    return s[max(0, min(len(s) - 1, -(-int(q * 100 * len(s)) // 100) - 1))]


def p50(v):
    s = sorted(v)
    return s[(len(s) - 1) // 2]  # lower median: always an observed rep


def ratio_ci(base, cand, seed=0):
    """Speedup = p50(base) / p50(cand), seeded bootstrap 90% CI. Same input, same interval."""
    rng = random.Random(seed)
    rs = sorted(p50([rng.choice(base) for _ in base]) / max(p50([rng.choice(cand) for _ in cand]), 1e-9) for _ in range(BOOT))
    lo, hi = (1 - CI) / 2, 1 - (1 - CI) / 2
    return {"ratio": round(p50(base) / max(p50(cand), 1e-9), 2), "lo": round(rs[int(lo * BOOT)], 2), "hi": round(rs[int(hi * BOOT) - 1], 2)}


def dist(v):
    o = {"n": len(v), "p50": r(p50(v)), "p95": r(pct(v, 0.95))}
    if len(v) < 20:
        o["p95_low_n"] = True  # nearest-rank p95 of <20 reps is the max, not a tail estimate
    return o


# ---------- report ----------

COMPARE_META = ("device", "debug", "build", "platform", "journey")


def report(cfg, traces, base=None):
    o = _opts(cfg)
    main = o["main"]
    arms = {}
    for tr in traces:
        arms.setdefault(str(tr["meta"].get("impl", "unknown")), []).append(tr)
    names = sorted(arms)
    base = base or o["base"] or names[0]
    if base not in arms:
        raise ValueError(f"base arm {base!r} not in traces (arms: {', '.join(names)})")
    names = [base] + [n for n in names if n != base]

    refused = []
    for k in COMPARE_META:
        vals = {n: sorted({json.dumps(t["meta"].get(k), sort_keys=True) for t in arms[n]}) for n in names}
        if len({v for vs in vals.values() for v in vs}) > 1:
            refused.append(f"meta.{k} differs: " + "; ".join(f"{n}={','.join(vals[n])}" for n in names))
    fx = {n: sorted({json.dumps(t["fixtures"], sort_keys=True) for t in arms[n]}) for n in names}
    if len({v for vs in fx.values() for v in vs}) > 1:
        refused.append("fixtures differ: " + "; ".join(f"{n}={','.join(fx[n])}" for n in names))

    # per arm, per window: one row per rep
    rows = {n: {} for n in names}
    for n in names:
        for tr in arms[n]:
            sc = screens(tr)
            for scr, m in sc.items():
                st = settled(tr, m["interactive"], main)
                mil = {k: r(v - m["tap"]) for k, v in m.items() if k != "tap"}
                if st is not None:
                    mil["settled"] = r(st - m["tap"])
                rows[n].setdefault(scr, []).append({"milestones": mil, **rep_window(tr, [(m["tap"], m["interactive"])], main, m["tap"])})
            if len(sc) > 1:  # journey: app time = sum of screen windows, in tap order
                ws = sorted((m["tap"], m["interactive"]) for m in sc.values())
                x = rep_window(tr, ws, main, ws[0][0])
                rows[n].setdefault("@journey", []).append({"milestones": {"interactive": x["window_ms"], "wall": x["wall_ms"]}, **x})

    phase_order = o["phases"] + sorted({k for n in names for w in rows[n].values() for x in w for k in x["buckets"]} - set(o["phases"]) - {UNINSTRUMENTED, WAITING}) + [UNINSTRUMENTED, WAITING]
    windows = []
    for w in sorted({w for n in names for w in rows[n]}, key=lambda w: (w != "@journey", w)):
        entry = {"window": w, "arms": {}}
        for n in names:
            reps = sorted(rows[n].get(w, []), key=lambda x: (x["milestones"]["interactive"], x["trace"]))
            if not reps:
                continue
            ms = sorted({k for x in reps for k in x["milestones"]})
            med = reps[(len(reps) - 1) // 2]
            entry["arms"][n] = {
                "n": len(reps),
                "milestones": {k: dist([x["milestones"][k] for x in reps if k in x["milestones"]]) for k in ms},
                "main_busy": dist([x["main_busy_ms"] for x in reps]),
                "buckets_p50": {k: r(p50([x["buckets"].get(k, 0) for x in reps])) for k in phase_order if any(k in x["buckets"] for x in reps)},
                "median_rep": med,
            }
        if not refused and base in entry["arms"]:
            b0 = entry["arms"][base]
            for n in names[1:]:
                if n not in entry["arms"]:
                    continue
                pick = lambda arm, k: [x["milestones"][k] for x in rows[arm][w] if k in x["milestones"]]
                sp = {k: ratio_ci(pick(base, k), pick(n, k)) for k in b0["milestones"] if k != "wall" and k in entry["arms"][n]["milestones"] and pick(n, k)}
                sp["main_busy"] = ratio_ci([x["main_busy_ms"] for x in rows[base][w]], [x["main_busy_ms"] for x in rows[n][w]])
                entry.setdefault("speedup", {})[n] = sp
        windows.append(entry)

    return {
        "version": VERSION, "base": base, "arms": [{"impl": n, "reps": len(arms[n]), "meta": _common(arms[n])} for n in names],
        "comparable": not refused, "refused": refused, "phases": phase_order, "labels": o["labels"], "windows": windows,
        "clock": {t["name"]: t["clock"] for t in traces if t["clock"]},
        "errors": [{"trace": t["name"], "detail": e} for t in traces for e in t["errors"]],
    }


def _common(trs):
    keys = set.intersection(*(set(t["meta"]) for t in trs))
    return {k: trs[0]["meta"][k] for k in sorted(keys) if all(t["meta"][k] == trs[0]["meta"][k] for t in trs)}


def render(o):
    L = o["labels"]
    lab = lambda k: L.get(k, k)
    out = [f"perf: base {o['base']}  arms " + ", ".join(f"{a['impl']} ({a['reps']} reps)" for a in o["arms"])]
    if o["refused"]:
        out += ["NOT COMPARABLE (no speedups):"] + [f"  {x}" for x in o["refused"]]
    for w in o["windows"]:
        out.append(f"\n{'journey' if w['window'] == '@journey' else 'screen ' + w['window']}")
        arms = list(w["arms"])
        for k in SHOWN:
            vals = [w["arms"][a]["milestones"].get(k) for a in arms]
            if any(vals):
                out.append(f"  {k:<14}" + "".join(f"{a} p50 {v['p50']:>7}ms  " if v else f"{a} {'-':>11}  " for a, v in zip(arms, vals)))
        out.append(f"  {'main busy':<14}" + "".join(f"{a} p50 {w['arms'][a]['main_busy']['p50']:>7}ms  " for a in arms))
        for n, sp in w.get("speedup", {}).items():
            out.append(f"  speedup {n} vs {o['base']}: " + ", ".join(f"{k} {v['ratio']}x [{v['lo']}-{v['hi']}]" for k, v in sp.items()))
        out.append("  where the time goes (median rep, exclusive, sums to tap->interactive):")
        width = max(len(lab(ph)) for ph in o["phases"]) + 2
        for ph in o["phases"]:
            vals = [w["arms"][a]["median_rep"]["buckets"].get(ph) for a in arms]
            if any(v is not None for v in vals):
                out.append(f"    {lab(ph):<{width}}" + "".join(f"{a} {(v or 0):>7}ms  " for a, v in zip(arms, vals)))
    if o["errors"]:
        out += [f"\nerrors ({len(o['errors'])}):"] + [f"  {e['trace']}: {e['detail']}" for e in o["errors"]]
    return "\n".join(out)


# ---------- HTML ----------

PALETTE = ["#4C7DFF", "#1BAA8E", "#E0A030", "#C2549D", "#7A63D9", "#3FA7C9", "#D9643B", "#6E9B3A"]


def html_report(o):
    """Self-contained page: per window, milestone table, bucket bars and synced timelines on one time scale."""
    e = html.escape
    L = o["labels"]
    color = {p: PALETTE[i % len(PALETTE)] for i, p in enumerate(x for x in o["phases"] if x not in (UNINSTRUMENTED, WAITING))}
    color[UNINSTRUMENTED], color[WAITING] = "var(--unins)", "var(--wait)"
    legend = "".join(f'<span class="lg"><i style="background:{color[p]}"></i>{e(L.get(p, p))}</span>' for p in o["phases"])
    parts = []
    for w in o["windows"]:
        arms = list(w["arms"])
        title = "Journey" if w["window"] == "@journey" else f"Screen: {w['window']}"
        head = "".join(f"<th>{e(a)} p50</th><th>p95</th>" for a in arms)
        trs = []
        for k in SHOWN:
            if not any(k in w["arms"][a]["milestones"] for a in arms):
                continue
            cells = "".join((f"<td>{v['p50']}</td><td class=dim>{v['p95']}{'*' if v.get('p95_low_n') else ''}</td>" if (v := w["arms"][a]["milestones"].get(k)) else "<td>-</td><td>-</td>") for a in arms)
            sp = "".join(f"<td><b>{s[k]['ratio']}×</b> <span class=dim>[{s[k]['lo']}–{s[k]['hi']}]</span></td>" for s in w.get("speedup", {}).values() if k in s)
            trs.append(f"<tr><td>{e(k.replace('_', ' '))}</td>{cells}{sp}</tr>")
        mb = "".join(f"<td>{w['arms'][a]['main_busy']['p50']}</td><td class=dim>{w['arms'][a]['main_busy']['p95']}</td>" for a in arms)
        msp = "".join(f"<td><b>{s['main_busy']['ratio']}×</b></td>" for s in w.get("speedup", {}).values())
        trs.append(f"<tr><td>main thread busy</td>{mb}{msp}</tr>")
        sph = "".join(f"<th>speedup {e(n)}</th>" for n in w.get("speedup", {}))
        table = f"<table><tr><th>ms</th>{head}{sph}</tr>{''.join(trs)}</table>"
        scale = max(w["arms"][a]["median_rep"]["window_ms"] for a in arms) or 1
        bars = []
        for a in arms:
            m = w["arms"][a]["median_rep"]
            segs = "".join(f'<i style="width:{v / scale * 100:.3f}%;background:{color.get(p, "#999")}" title="{e(L.get(p, p))}: {v} ms"></i>'
                           for p in o["phases"] if (v := m["buckets"].get(p)))
            bars.append(f'<div class=bar><span class=arm>{e(a)}</span><div class=track>{segs}</div><span class=num>{m["window_ms"]} ms</span></div>')
        span = max(max([w["arms"][a]["median_rep"]["wall_ms"]] + list(w["arms"][a]["median_rep"]["milestones"].values())) for a in arms) or 1
        tl = "".join(_timeline(a, w["arms"][a], span, color, L) for a in arms)  # one time scale for every arm
        parts.append(f"<section><h2>{e(title)}</h2>{table}<h3>Where the time goes</h3><p class=dim>Median rep per arm. Exclusive: every ms is in one bucket, so a bar is tap → interactive. Overlapped work is in the timeline, not the bar.</p>{''.join(bars)}<h3>Timeline</h3>{tl}</section>")
    warn = "".join(f"<p class=warn>{e(x)}</p>" for x in o["refused"])
    errs = "".join(f"<li>{e(x['trace'])}: {e(x['detail'])}</li>" for x in o["errors"])
    arms_txt = " vs ".join(f"{e(a['impl'])} ({a['reps']} reps)" for a in o["arms"])
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PerfStack report</title><style>
:root{{--bg:#fff;--fg:#1a1c20;--dim:#6b7078;--line:#e3e5e8;--card:#f6f7f9;--unins:#9aa0a8;--wait:repeating-linear-gradient(45deg,#d5d8dc 0 4px,#eceef0 4px 8px)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#121417;--fg:#e8eaed;--dim:#9aa0a8;--line:#2a2e33;--card:#1b1e22;--unins:#5d636b;--wait:repeating-linear-gradient(45deg,#2a2e33 0 4px,#1f2226 4px 8px)}}}}
:root[data-theme=dark]{{--bg:#121417;--fg:#e8eaed;--dim:#9aa0a8;--line:#2a2e33;--card:#1b1e22;--unins:#5d636b;--wait:repeating-linear-gradient(45deg,#2a2e33 0 4px,#1f2226 4px 8px)}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}}
h1{{font-size:20px}}h2{{font-size:17px;margin:28px 0 8px}}h3{{font-size:14px;margin:18px 0 6px}}.dim{{color:var(--dim)}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;display:block;overflow-x:auto}}td,th{{padding:4px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}
.bar{{display:flex;align-items:center;gap:8px;margin:4px 0}}.arm{{width:80px;flex:none}}.num{{width:80px;flex:none;text-align:right;font-variant-numeric:tabular-nums}}
.track{{flex:1;display:flex;height:18px;background:var(--card);border-radius:3px;overflow:hidden}}.track i{{display:block;height:100%}}
.lg{{display:inline-flex;align-items:center;gap:4px;margin-right:12px}}.lg i{{width:10px;height:10px;border-radius:2px;display:inline-block}}
.tl{{margin:6px 0 14px}}.tl svg{{width:100%;height:auto;background:var(--card);border-radius:4px}}.tl text{{fill:var(--fg);font-size:10px}}.tl .ax{{fill:var(--dim)}}
.warn{{background:#ffefe0;color:#7a3b00;padding:8px;border-radius:4px}}
</style></head><body><h1>PerfStack report</h1><p class=dim>{arms_txt}. Base: {e(o['base'])}. p50 latency, lower is better; * = fewer than 20 reps, p95 is the max.</p>
{warn}<p>{legend}</p>{''.join(parts)}{f'<h2>Trace errors</h2><ul>{errs}</ul>' if errs else ''}</body></html>
"""


def _timeline(arm, a, scale, color, L):
    e = html.escape
    m = a["median_rep"]
    lanes, rows = {}, []
    for s in m["spans"]:  # lane = thread, then depth; main first
        lanes.setdefault((s["thread"] != "main", s["thread"], s["depth"]), []).append(s)
    W, H, top, x0 = 1000, 16, 18, 90
    px = lambda t: x0 + t / scale * (W - x0 - 10)
    y = top
    for (_, th, depth), ss in sorted(lanes.items()):
        rows.append(f'<text x=4 y="{y + 11}" class=ax>{e(th)}{" ·" * depth}</text>')
        for s in ss:
            x, w = px(s["start_ms"]), max(px(s["end_ms"]) - px(s["start_ms"]), 1)
            tip = f"{s['name']} · {L.get(s['phase'], s['phase'])} · {th} · {r(s['end_ms'] - s['start_ms'])} ms (self {s['self_ms']} ms)"
            lbl = f'<text x="{x + 3:.1f}" y="{y + 11}">{e(s["name"])}</text>' if w > 60 else ""
            rows.append(f'<g><title>{e(tip)}</title><rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{H - 3}" rx=2 fill="{color.get(s["phase"], "#999")}" opacity=.85></rect>{lbl}</g>')
        y += H
    if m.get("busy"):  # main-runloop busy stretches: the work no span names
        rows.append(f'<text x=4 y="{y + 11}" class=ax>main busy</text>')
        rows += [f'<g><title>main busy {r(q - p_)} ms</title><rect x="{px(p_):.1f}" y="{y}" width="{max(px(q) - px(p_), 1):.1f}" height="{H - 3}" rx=2 fill="var(--unins)"></rect></g>'
                 for p_, q in m["busy"]]
        y += H
    ticks = "".join(f'<line x1="{px(t):.1f}" x2="{px(t):.1f}" y1="{top - 4}" y2="{y}" stroke="currentColor" opacity=.12></line><text x="{px(t):.1f}" y=12 class=ax>{t:g}ms</text>'
                    for t in _ticks(scale))
    # journey milestones are sums over screens, not moments on this axis: no lines
    ms = []
    for v, k in ([] if m.get("between_ms") else sorted((v, k) for k, v in m["milestones"].items())):
        if ms and px(v) - px(ms[-1][0]) < 4:  # same moment: one line, one label
            ms[-1] = (ms[-1][0], ms[-1][1] + " · " + k)
        else:
            ms.append((v, k))
    anchor = lambda x: f'x="{x - 2:.1f}" text-anchor=end' if x > W - 90 else f'x="{x + 2:.1f}"'
    marks = "".join(f'<line x1="{px(v):.1f}" x2="{px(v):.1f}" y1="{top - 4}" y2="{y}" stroke="currentColor"{"" if "interactive" in k else " stroke-dasharray=\'3 3\'"}></line>'
                    f'<text {anchor(px(v))} y="{y + 11 + 11 * (i % 3)}" class=ax>{e(k)}</text>' for i, (v, k) in enumerate(ms))  # labels staggered on 3 rows
    note = f" · {m['between_ms']} ms between screens not counted" if m.get("between_ms") else ""
    hidden = f" · {m['spans_dropped']} short spans hidden" if m["spans_dropped"] else ""
    return f'<div class=tl><b>{e(arm)}</b> <span class=dim>{e(m["trace"])} · app time {m["window_ms"]} ms{note}{hidden}</span><svg viewBox="0 0 {W} {y + 38}" role=img aria-label="{e(arm)} timeline">{ticks}{"".join(rows)}{marks}</svg></div>'


def _ticks(scale):
    step = next(s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 1e9) if scale / s <= 8)
    return [i * step for i in range(int(scale // step) + 1)]
