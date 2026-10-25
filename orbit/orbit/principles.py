"""L3 rule evaluation (spec 8.6, 9.1). Closed predicate set; every predicate reads only the index (R2)."""
import fnmatch
import json
import re
from pathlib import Path

from . import VERSION, index

_SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "principle.schema.json").read_text())
REQUIRED = tuple(_SCHEMA["required"])  # the schema is the single source for what a principle must carry
STATUSES = tuple(_SCHEMA["properties"]["status"]["enum"])


def load(cfg):
    data, h = cfg.load_own("principles")
    ps = data.get("principle", [])
    for p in ps:
        missing = [k for k in REQUIRED if k not in p]
        if missing:
            raise ValueError(f"principle {p.get('id', '?')}: missing {missing}")
        if p["status"] not in STATUSES:
            raise ValueError(f"principle {p['id']}: status must be one of {STATUSES}")
        if p["predicate"].get("kind") not in PREDICATES:
            raise ValueError(f"principle {p['id']}: unknown predicate {p['predicate'].get('kind')!r}")
    return sorted(ps, key=lambda p: p["id"]), h


def applies(unit, principle):
    _, kind, name, _ = unit
    sc = principle["scope"]
    return (not sc.get("unit_kinds") or kind in sc["unit_kinds"]) and any(fnmatch.fnmatchcase(name, g) for g in sc.get("units", ["*"]))


def _scoped(conn, principle):
    return [u for u in conn.execute("SELECT id, kind, name, root_path FROM units ORDER BY id") if applies(u, principle)]


def _layers_clause(pred):
    ls = pred.get("layers")
    return (" AND layer IN (%s)" % ",".join("?" * len(ls)), list(ls)) if ls else ("", [])


def _layers_present(conn, p, pred):
    for uid, _, _, root in _scoped(conn, p):
        have = {r[0] for r in conn.execute("SELECT DISTINCT layer FROM listing WHERE unit = ?", (uid,))}
        for l in pred["layers"]:
            if l not in have:
                yield root, 1, f"{uid} lacks layer {l}"


def _fact_match(kind, noun):
    def run(conn, p, pred):
        rx = re.compile(pred["pattern"])
        clause, extra = _layers_clause(pred)
        for uid, *_ in _scoped(conn, p):
            for name, file, line in conn.execute(
                    f"SELECT name, file, line FROM facts WHERE unit = ? AND kind = '{kind}'{clause} ORDER BY file, line, name", [uid, *extra]):
                if rx.search(name):
                    yield file, line, f"{noun} {name}"
    return run


def _max_loc(conn, p, pred):
    clause, extra = _layers_clause(pred)
    for uid, *_ in _scoped(conn, p):
        for path, loc in conn.execute(
                f"SELECT l.path, f.loc FROM listing l JOIN files f ON f.path = l.path WHERE l.unit = ? AND f.lang IS NOT NULL "
                f"AND f.lang != 'markdown' AND f.loc > ?{clause.replace('layer', 'l.layer')} ORDER BY l.path", [uid, pred["max"], *extra]):
            yield path, 1, f"{loc} lines > {pred['max']}"


def _single_conformer(conn, p, pred):
    rx, limit = re.compile(pred["protocol"]), pred.get("max", 1)
    for uid, *_ in _scoped(conn, p):
        rows = [(f, ln, json.loads(d)["type"], n) for n, f, ln, d in conn.execute(
            "SELECT name, file, line, data FROM facts WHERE unit = ? AND kind = 'protocol' ORDER BY file, line", (uid,)) if rx.search(n)]
        for i, (f, ln, t, _) in enumerate(rows[limit:], limit + 1):
            yield f, ln, f"{t} is conformer #{i} of {pred['protocol']} (max {limit})"


PREDICATES = {
    "layers_present": _layers_present,
    "forbid_import": _fact_match("import", "imports"),
    "forbid_symbol": _fact_match("ref", "references"),
    "forbid_ui_literal": _fact_match("ui_literal", "shows"),
    "max_loc": _max_loc,
    "single_conformer": _single_conformer,
}


def check(cfg, conn):
    ps, h = load(cfg)
    results, obs, enf = [], 0, 0
    for p in ps:
        vs = sorted(({"principle": p["id"], "file": f, "line": ln, "detail": d} for f, ln, d in PREDICATES[p["predicate"]["kind"]](conn, p, p["predicate"])),
                    key=lambda v: (v["file"], v["line"], v["detail"]))
        results.append({"id": p["id"], "status": p["status"], "statement": p["statement"], "violations": vs})
        if p["status"] == "enforced":
            enf += len(vs)
        else:
            obs += len(vs)
    return {"version": VERSION, "config_hash": cfg.hash, "principles_hash": h, "index_digest": index.digest(conn),
            "principles": results, "summary": {"principles": len(ps), "observed_violations": obs, "enforced_violations": enf}}


def render(o, limit=5):
    out = []
    for r in o["principles"]:
        out.append(f"{r['id']:<28} {r['status']:<9} {len(r['violations']):>4} violations  {r['statement']}")
        out += [f"    {v['file']}:{v['line']}  {v['detail']}" for v in r["violations"][:limit]]
        if len(r["violations"]) > limit:
            out.append(f"    ... {len(r['violations']) - limit} more (--json)")
    s = o["summary"]
    out.append(f"\n{s['principles']} principles, observed violations {s['observed_violations']}, enforced violations {s['enforced_violations']}")
    return "\n".join(out)


def predicate_text(p):
    d = dict(p["predicate"])
    return f"{d.pop('kind')}({', '.join(f'{k}={v}' for k, v in sorted(d.items()))})"
