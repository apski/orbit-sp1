"""L3 layer diagram of one unit: which layer imports which, read from the index (R2).

Nodes are the unit's layers plus the shell modules its forbid_import principles talk about
(so pandas never shows, `services.db` does). An edge carries the import sites and any
principle it violates. Mermaid output has no counts, so the committed diagram only changes
when a dependency between layers appears or disappears: that is the architectural change
a reviewer should see.
"""
import re

from . import VERSION, index, principles


def _prefix(conn, root):
    """Module name of the unit root: the longest dotted suffix of its path that some import uses."""
    parts = root.split("/")
    for i in range(len(parts)):
        mod = ".".join(parts[i:])
        if conn.execute("SELECT 1 FROM facts WHERE kind = 'import' AND (name = ? OR name LIKE ?) LIMIT 1",
                        (mod, mod + ".%")).fetchone():
            return mod
    return ".".join(parts)


def _resolve(name, file, root, prefix):
    """Absolute module for an import fact; relative imports resolve against the importing file."""
    if not name.startswith("."):
        return name
    dots = len(name) - len(name.lstrip("."))
    pkg = file[len(root) + 1:].split("/")[:-1]
    pkg = pkg[:len(pkg) - (dots - 1)] if dots > 1 else pkg
    return ".".join([prefix, *pkg, *filter(None, name[dots:].split("."))])


def _layer_of(mod, prefix, layers, root_layer):
    if mod == prefix:
        return root_layer
    if not mod.startswith(prefix + "."):
        return None
    head = mod[len(prefix) + 1:].split(".")[0]
    return head if head in layers else root_layer


def _external(mod, prefix):
    """Shell node name: `services.db` for siblings under the same namespace, else the top package."""
    ns = prefix.split(".")[0]
    parts = mod.split(".")
    return ".".join(parts[:2]) if parts[0] == ns and len(parts) > 1 else parts[0]


def build(cfg, conn, unit):
    uid, _, _, root = unit
    root_layer = cfg.d.get("platform", {}).get("root_layer", "_root")
    prefix = _prefix(conn, root)
    layers = {l: {"id": l, "kind": "layer", "files": n, "loc": loc} for l, n, loc in conn.execute(
        "SELECT l.layer, COUNT(*), COALESCE(SUM(f.loc), 0) FROM listing l LEFT JOIN files f ON f.path = l.path "
        "WHERE l.unit = ? GROUP BY l.layer ORDER BY l.layer", (uid,))}

    ps, _ = principles.load(cfg)
    scoped = [p for p in ps if p["predicate"]["kind"] == "forbid_import" and principles.applies(unit, p)]
    shell = [re.compile(p["predicate"]["pattern"]) for p in scoped]
    bad = {}
    for r in principles.check(cfg, conn)["principles"]:
        for v in r["violations"]:
            bad.setdefault((v["file"], v["line"]), []).append(r["id"])

    nodes, edges = dict(layers), {}
    for name, file, line, layer in conn.execute(
            "SELECT name, file, line, layer FROM facts WHERE unit = ? AND kind = 'import' ORDER BY file, line, name", (uid,)):
        mod = _resolve(name, file, root, prefix)
        to = _layer_of(mod, prefix, layers, root_layer)
        if to is None:
            if not any(rx.search(mod) for rx in shell):
                continue
            to = "shell:" + _external(mod, prefix)  # namespaced: app/utils is not the unit's own utils layer
            nodes.setdefault(to, {"id": to, "kind": "shell", "files": 0, "loc": 0})
        if to == layer:
            continue
        e = edges.setdefault((layer, to), {"from": layer, "to": to, "count": 0, "sites": [], "violations": []})
        e["count"] += 1
        e["sites"].append(f"{file}:{line}")
        e["violations"] += [{"principle": p, "site": f"{file}:{line}"} for p in bad.get((file, line), [])]

    o = {"version": VERSION, "config_hash": cfg.hash, "index_digest": index.digest(conn), "unit": uid, "root": root,
         "module": prefix, "nodes": sorted(nodes.values(), key=lambda n: (n["kind"], n["id"])),
         "edges": [edges[k] for k in sorted(edges)]}
    o["mermaid"] = mermaid(o)
    return o


def _nid(s):
    return "n_" + "".join(c if c.isalnum() else "_" for c in s)


def mermaid(o):
    out = ["flowchart LR", f"  subgraph unit[\"{o['unit']}\"]"]
    out += [f"    {_nid(n['id'])}[\"{n['id']}\"]" for n in o["nodes"] if n["kind"] == "layer"]
    out.append("  end")
    out += [f"  {_nid(n['id'])}[(\"{n['id'][6:]}\")]" for n in o["nodes"] if n["kind"] == "shell"]
    red = []
    for i, e in enumerate(o["edges"]):
        out.append(f"  {_nid(e['from'])} --> {_nid(e['to'])}")
        if e["violations"]:
            red.append(str(i))
    if red:
        out.append(f"  linkStyle {','.join(red)} stroke:#d33,stroke-width:2px")
    return "\n".join(out)


def render(o):
    v = sum(1 for e in o["edges"] if e["violations"])
    return f"%% {o['unit']} ({o['module']}), {len(o['edges'])} edges, {v} violating. index {o['index_digest']}\n{o['mermaid']}"
