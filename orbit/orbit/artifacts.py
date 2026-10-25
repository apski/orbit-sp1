"""L3 artifact ladder (spec 7.1): present / missing / stale / derived per unit, from the index only.

ponytail: stale compares file mtimes (index `files.mtime`); a fresh clone makes everything look equally
new. Upgrade path: stamp the git commit time of each path into the index.
"""
import glob
import re

from . import VERSION, index


def _templates(art, unit):
    _, _, name, root = unit
    sub = {"root": glob.escape(root), "name": glob.escape(name), "snake": glob.escape(name.replace("-", "_"))}
    return [t.format(**sub) for t in art["paths"]]


def status(cfg, conn, unit):
    uid, kind, _, _ = unit
    files = {p: (size, mtime, u) for p, size, mtime, u in conn.execute(
        "SELECT l.path, f.size, f.mtime, l.unit FROM listing l JOIN files f ON f.path = l.path")}
    out = []
    for art in cfg.d["artifacts"]:
        if art.get("unit_kinds") and kind not in art["unit_kinds"]:
            continue
        row = {"name": art["name"], "kind": art["kind"], "level": art["level"], "status": "derived", "count": 0, "paths": []}
        if art["kind"] != "derived":
            rxs = [re.compile(glob.translate(t, recursive=True, include_hidden=True)) for t in _templates(art, unit)]
            hit = sorted(p for p, (size, _, _) in files.items() if size > 0 and any(r.match(p) for r in rxs))
            row["count"], row["paths"] = len(hit), hit[:5]
            if not hit:
                row["status"] = "missing"
            else:
                newest_art, hitset = max(files[p][1] for p in hit), set(hit)
                newest_src = max((m for p, (_, m, u) in files.items() if u == uid and p not in hitset), default=0)
                stale = art.get("stale_if_source_newer") and newest_src > newest_art
                row["status"] = "stale" if stale else "present"
        out.append(row)
    return {"version": VERSION, "config_hash": cfg.hash, "index_digest": index.digest(conn), "unit": uid, "artifacts": out}


def render(o):
    out = [f"{o['unit']}"]
    for a in o["artifacts"]:
        out.append(f"  {a['name']:<8} {a['level']:<8} {a['status']:<8} {a['count']:>3}  {', '.join(a['paths'][:2])}")
    return "\n".join(out)
