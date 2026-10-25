"""CLI. Commands never call each other (R6); each wraps one L3 function, logs the request (R9), emits text or --json."""
import argparse
import json
import os
import sys
import time

from . import VERSION, artifacts, diagram, index, observe, packet, perf, principles, reqlog, router, sources, verify
from .config import Config


def dump(o):
    return json.dumps(o, indent=2, sort_keys=True)


def _unit(conn, token):
    return index.resolve_unit(conn, token)


def status(cfg, a):
    conn, _ = index.ensure(cfg)
    units = [{"id": u, "files": n} for u, n in conn.execute(
        "SELECT u.id, (SELECT COUNT(*) FROM listing l WHERE l.unit = u.id) FROM units u ORDER BY u.id")]
    langs = [{"name": n or "-", "files": c, "ok": ok, "partial": pa, "skipped": sk} for n, c, ok, pa, sk in conn.execute(
        "SELECT lang, COUNT(*), SUM(parse_status='ok'), SUM(parse_status='partial'), SUM(parse_status='skipped') FROM files GROUP BY lang ORDER BY lang")]
    o = {"version": VERSION, "config_hash": cfg.hash, "index_digest": index.digest(conn), "units": units, "languages": langs,
         "sources": {k: v["status"] for k, v in sources.read(cfg).items()}}
    text = [f"{cfg.name}  config {o['config_hash']}  index {o['index_digest']}", f"\nunits ({len(units)}):"]
    kinds = {}
    for u in units:
        kinds.setdefault(u["id"].split(":")[0], []).append(u)
    text += [f"  {k:<16} {len(v):>3} units, {sum(x['files'] for x in v):>5} files" for k, v in sorted(kinds.items())]
    text += ["\nlanguages:"] + [f"  {l['name']:<12} {l['files']:>5} files  ok {l['ok']}  partial {l['partial']}  skipped {l['skipped']}" for l in langs]
    text += ["\nsources:"] + [f"  {k:<16} {v}" for k, v in o["sources"].items()]
    return o, "\n".join(text), 0, {}


def cmd_index(cfg, a):
    conn, st = index.ensure(cfg, force=a.rebuild)
    nf, nt, nu = (conn.execute(q).fetchone()[0] for q in ("SELECT COUNT(*) FROM files", "SELECT COUNT(*) FROM facts", "SELECT COUNT(*) FROM units"))
    o = {"version": VERSION, "config_hash": cfg.hash, "digest": index.digest(conn), "files": nf, "facts": nt, "units": nu}
    print(f"parsed={st['parsed']}", file=sys.stderr)  # varies run to run, so it stays off stdout
    return o, f"index: files={nf} facts={nt} units={nu} digest={o['digest']}", 0, {"parsed": st["parsed"]}


def cmd_observe(cfg, a):
    conn, _ = index.ensure(cfg)
    o = observe.observe(cfg, conn, _unit(conn, a.unit))
    return o, observe.render(o, a.limit), 0, {}


def cmd_refs(cfg, a):
    conn, _ = index.ensure(cfg)
    o = observe.refs(conn, a.symbol, a.limit)
    return o, observe.render_refs(o), 0, {}


def cmd_how(cfg, a):
    conn, _ = index.ensure(cfg)
    q = " ".join(a.question)
    o = router.how(cfg, conn, q)
    return o, router.render(o), 0, {"question": q, "route": o["route"], "suggested": (o["command"] or "").split()[1:2] and o["command"].split()[1],
                                     "outcome": "ok" if o["route"] else "no-match"}


def cmd_review(cfg, a):
    o = reqlog.review(cfg, a.days)
    return o, reqlog.render(o), 0, {}


def cmd_artifacts(cfg, a):
    conn, _ = index.ensure(cfg)
    o = artifacts.status(cfg, conn, _unit(conn, a.unit))
    return o, artifacts.render(o), 0, {}


def cmd_principles(cfg, a):
    conn, _ = index.ensure(cfg)
    o = principles.check(cfg, conn)
    bad = o["summary"]["enforced_violations"]
    return o, principles.render(o), 1 if bad else 0, {"outcome": "violations" if bad else "ok"}


def cmd_diagram(cfg, a):
    conn, _ = index.ensure(cfg)
    o = diagram.build(cfg, conn, _unit(conn, a.unit))
    return o, o["mermaid"] if a.mermaid else diagram.render(o), 0, {}


def cmd_packet(cfg, a):
    conn, _ = index.ensure(cfg)
    unit = _unit(conn, a.unit)
    tier = a.tier
    if not tier:
        r, _, _ = router.route(cfg, a.task)
        tier = r["tier"] if r and r["tier"] != "none" else "build"
    b = cfg.d["budget"]
    o = packet.build(cfg, conn, unit, a.task, tier, a.budget or b.get(tier, b["default"]))
    return o, o["markdown"], 0, {}


def cmd_perf(cfg, a):
    o = perf.report(cfg, perf.load(a.traces), a.base)
    if a.html:
        with open(a.html, "w") as f:
            f.write(perf.html_report(o))
    return o, perf.render(o), 0, {"outcome": "ok" if o["comparable"] else "not-comparable"}


def cmd_verify(cfg, a):
    o = verify.verify(cfg, a.target)
    return o, verify.render(o), o["exit"], {"outcome": "ok" if not o["exit"] else "failed"}


def build_parser():
    ap = argparse.ArgumentParser(prog="orbit", description="Deterministic codebase engine: index, observe, route, packet, verify.")
    ap.add_argument("--config", help="path to orbit.toml (default: $ORBIT_CONFIG, which the repo's .orbit/orbit launcher sets)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, *pos):
        p = sub.add_parser(name, help=help_)
        for n, kw in pos:
            p.add_argument(n, **kw)
        p.add_argument("--json", action="store_true", help="machine-readable output (schemas/)")
        p.set_defaults(fn=fn)
        return p

    add("status", status, "units, languages, sources")
    add("index", cmd_index, "refresh the index (incremental)").add_argument("--rebuild", action="store_true")
    add("observe", cmd_observe, "layers, imports, types of one unit", ("unit", {})).add_argument("--limit", type=int, default=40)
    add("refs", cmd_refs, "where a symbol is defined and used", ("symbol", {})).add_argument("--limit", type=int, default=50)
    add("how", cmd_how, "route a question to a command", ("question", {"nargs": "+"}))
    add("review", cmd_review, "weekly review of the request log").add_argument("--days", type=int, default=7)
    add("artifacts", cmd_artifacts, "artifact ladder of one unit", ("unit", {}))
    pr = add("principles", cmd_principles, "check house rules", ("action", {"choices": ["check"]}))
    add("diagram", cmd_diagram, "layer dependency diagram of one unit (Mermaid)", ("unit", {})).add_argument(
        "--mermaid", action="store_true", help="bare Mermaid, as embedded in docs")
    pk = add("packet", cmd_packet, "budgeted, cited context for one task", ("unit", {}), ("task", {}))
    pk.add_argument("--tier", choices=["mechanical", "build", "explain"])
    pk.add_argument("--budget", type=int)
    pf = add("perf", cmd_perf, "journey -> screen -> phase attribution and A/B speedups over trace files", ("traces", {}))
    pf.add_argument("--base", help="baseline arm (meta.impl); default [perf].base, else the first arm by name")
    pf.add_argument("--html", help="also write a self-contained HTML report here")
    add("verify", cmd_verify, "run a check or check-set", ("target", {"nargs": "?", "default": "fast"}))
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    cfg = Config(a.config or os.environ.get("ORBIT_CONFIG"))
    t0 = time.monotonic()
    rec = {"command": a.cmd, "args": [x for x in (argv if argv is not None else sys.argv[1:]) if x != "--json"]}
    try:
        o, text, code, extra = a.fn(cfg, a)
    except (index.UnitError, ValueError, KeyError, FileNotFoundError) as e:
        reqlog.write(cfg, dict(rec, outcome="error", error=str(e), duration_ms=round((time.monotonic() - t0) * 1000)))
        print(f"orbit: {e}", file=sys.stderr)
        return 2
    print(dump(o) if a.json else text)
    extra = dict(extra)
    reqlog.write(cfg, dict(rec, outcome=extra.pop("outcome", "ok"), duration_ms=round((time.monotonic() - t0) * 1000), **extra))
    return code
