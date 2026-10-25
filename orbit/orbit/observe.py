"""L3 observation: layers, imports, types, protocols of one unit (schema: observation)."""
import json

from . import VERSION, index


def observe(cfg, conn, unit):
    uid, kind, name, root = unit
    layers = [{"name": l, "files": n, "loc": loc} for l, n, loc in conn.execute(
        "SELECT l.layer, COUNT(*), COALESCE(SUM(f.loc), 0) FROM listing l LEFT JOIN files f ON f.path = l.path "
        "WHERE l.unit = ? GROUP BY l.layer ORDER BY l.layer", (uid,))]
    imports = {}
    for mod, file, line, layer in conn.execute(
            "SELECT name, file, line, layer FROM facts WHERE unit = ? AND kind = 'import' ORDER BY name, file, line", (uid,)):
        e = imports.setdefault(mod, {"module": mod, "count": 0, "layers": set(), "source": f"{file}:{line}"})
        e["count"] += 1
        e["layers"].add(layer)
    types = []
    for tname, file, line, layer, data in conn.execute(
            "SELECT name, file, line, layer, data FROM facts WHERE unit = ? AND kind = 'type' ORDER BY file, line, name", (uid,)):
        d = json.loads(data)
        types.append({"name": tname, "kind": d["kind"], "visibility": d["visibility"], "layer": layer, "source": f"{file}:{line}"})
    protocols = {}
    for base, file, line, data in conn.execute(
            "SELECT name, file, line, data FROM facts WHERE unit = ? AND kind = 'protocol' ORDER BY name, file, line", (uid,)):
        e = protocols.setdefault(base, {"name": base, "conformers": [], "source": f"{file}:{line}"})
        e["conformers"].append(json.loads(data)["type"])
    return {
        "version": VERSION, "config_hash": cfg.hash, "index_digest": index.digest(conn),
        "unit": uid, "root": root, "layers": layers,
        "imports": [dict(e, layers=sorted(e["layers"])) for e in sorted(imports.values(), key=lambda e: e["module"])],
        "types": types,
        "protocols": [dict(e, conformers=sorted(e["conformers"])) for e in sorted(protocols.values(), key=lambda e: e["name"])],
    }


def render(o, limit=40):
    out = [f"{o['unit']}  {o['root']}  (index {o['index_digest']})", "", "layers:"]
    out += [f"  {l['name']:<20} {l['files']:>4} files {l['loc']:>7} loc" for l in o["layers"]]
    top = sorted(o["imports"], key=lambda e: (-e["count"], e["module"]))
    out += ["", f"imports ({len(o['imports'])} distinct, top {min(limit, len(top))}):"]
    out += [f"  {e['module']:<44} x{e['count']:<3} {e['source']}" for e in top[:limit]]
    out += ["", f"types ({len(o['types'])}, first {min(limit, len(o['types']))}):"]
    out += [f"  {t['kind']:<9} {t['name']:<36} {t['layer']:<14} {t['source']}" for t in o["types"][:limit]]
    prot = sorted(o["protocols"], key=lambda e: (-len(e["conformers"]), e["name"]))
    out += ["", f"protocols/bases ({len(o['protocols'])}, top {min(limit, len(prot))}):"]
    out += [f"  {e['name']:<36} {len(e['conformers']):>3} conformers  {e['source']}" for e in prot[:limit]]
    return "\n".join(out)


def refs(conn, symbol, limit=50):
    defs = [{"kind": json.loads(d)["kind"], "unit": u, "source": f"{f}:{ln}"} for f, ln, u, d in conn.execute(
        "SELECT file, line, unit, data FROM facts WHERE kind = 'type' AND name = ? ORDER BY file, line", (symbol,))]
    uses = [{"unit": u, "source": f"{f}:{ln}"} for f, ln, u in conn.execute(
        "SELECT file, line, unit FROM refs WHERE symbol = ? ORDER BY file, line", (symbol,))]
    return {"version": VERSION, "symbol": symbol, "definitions": defs, "references": uses[:limit], "reference_count": len(uses),
            "index_digest": index.digest(conn)}


def render_refs(o):
    out = [f"{o['symbol']}: {len(o['definitions'])} definitions, {o['reference_count']} references"]
    out += [f"  def  {d['kind']:<9} {d['source']}  ({d['unit']})" for d in o["definitions"]]
    out += [f"  use  {r['source']}  ({r['unit']})" for r in o["references"]]
    if o["reference_count"] > len(o["references"]):
        out.append(f"  ... {o['reference_count'] - len(o['references'])} more (--limit)")
    return "\n".join(out)
