"""L4 packet (spec 7.2): the context for one agent and one task. Budgeted, ordered, every line cited (R10).

Order: mandatory (contract, invariants, verify), then files, facts, house rules. Stop at budget, record `dropped`.
Files are the code files that best match the task words (router.rank_files), in any unit of the same platform:
an iOS task touches Managers and Services as often as the screen unit it was routed to.
Tokens are estimated as ceil(chars / 4): deterministic and model-free.
"""
import fnmatch
import json
import math
import re
import subprocess

from . import VERSION, artifacts, index, principles, router


FILES = 10  # task-matched files a packet lists (~25 tokens each)


def est(text):
    return math.ceil(len(text) / 4)


def _words(text):
    return {w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", text)} - router.STOP


def _git(cfg, root):
    """Base commit and the dirty files under the unit root (so a busy tree does not leak into every packet)."""
    def run(*a):
        r = subprocess.run(["git", *a], cwd=cfg.root, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    prefix = (run("rev-parse", "--show-prefix") or "").strip()
    parts = (run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", root) or "").split("\0")
    dirty, skip = [], False
    for e in parts:
        if skip or not e:
            skip = False
            continue
        skip = e[0] in "RC" or e[1] in "RC"  # rename/copy: the next entry is the old path
        dirty.append(e[3:].removeprefix(prefix))
    base = (run("rev-parse", "--short=10", "HEAD") or "").strip() or None
    return {"base": base, "dirty_files": sorted(dirty)[:20], "dirty_count": len(dirty)}


def _headings(conn, path, task_words, n=6):
    """The headings of a spec doc the task touches (index facts), else its first headings. Pointers, not dumps."""
    rows = conn.execute("SELECT f.name, f.line, fl.hash FROM facts f JOIN files fl ON fl.path = f.file "
                        "WHERE f.file = ? AND f.kind = 'heading' ORDER BY f.line", (path,)).fetchall()
    scored = sorted(((-len(_words(h) & task_words), ln, h, rev) for h, ln, rev in rows))
    pick = [s for s in scored if s[0] < 0][:n] or sorted(scored, key=lambda s: s[1])[:n]
    return [(f"{path} L{ln}: {h}", f"{path}:{ln}") for _, ln, h, rev in sorted(pick, key=lambda s: s[1])]


def _spec_paths(cfg, unit, role):
    _, kind, name, _ = unit
    return [s["path"] for s in cfg.d.get("spec", []) if s["role"] == role
            and (not s.get("unit_kinds") or kind in s["unit_kinds"]) and any(fnmatch.fnmatchcase(name, g) for g in s.get("units", ["*"]))]


def build(cfg, conn, unit, task, tier, budget):
    uid, kind, name, root = unit
    tw = _words(task)
    S = []  # (title, mandatory, [(text, source)])

    contract = [l for p in _spec_paths(cfg, unit, "contract") for l in _headings(conn, p, tw)]
    if not contract:  # fall back to the unit's doc artifact, if it has one
        art = next((a for a in artifacts.status(cfg, conn, unit)["artifacts"] if a["name"] == "doc" and a["paths"]), None)
        contract = _headings(conn, art["paths"][0], tw) if art else []
    S.append(("Contract", True, contract or [(f"MISSING: no spec doc for {uid}; do not invent one", "orbit.toml:spec")]))

    inv = [l for p in _spec_paths(cfg, unit, "invariants") for l in _headings(conn, p, tw, 8)]
    ps, _ = principles.load(cfg)
    inv += [(f"{p['id']}: {p['statement']}", f"principles.toml#{p['id']}") for p in ps if p["status"] == "enforced" and principles.applies(unit, p)]
    S.append(("Invariants", True, inv or [(f"none declared for {uid}", "orbit.toml:spec")]))

    checks = [(f"{cid}: {' '.join(c['command']) if 'command' in c else 'planned'} ({'required' if c.get('required') else 'optional'})", f"orbit.toml:checks.{cid}")
              for cid, c in sorted(cfg.d["checks"].items()) if not c.get("unit_kinds") or kind in c["unit_kinds"]]
    S.append(("Verify", True, checks))

    plat = router.platform_kinds(conn, {kind.split("-")[0]})
    S.append(("Files", False, [(f"{path} ({u})", path) for _, path, u in router.rank_files(cfg, conn, task, plat)[:FILES]]))

    layers = conn.execute("SELECT l.layer, COUNT(*), COALESCE(SUM(f.loc), 0) FROM listing l LEFT JOIN files f ON f.path = l.path WHERE l.unit = ? GROUP BY l.layer ORDER BY l.layer", (uid,)).fetchall()
    hot = {l for l, _, _ in layers if _words(l) & tw}  # a layer named in the task is where the work is
    facts = [(f"layer {l}: {n} files, {loc} loc" + (" (named in task)" if l in hot else ""), f"index:{uid}/layers/{l}") for l, n, loc in layers if l in hot or len(layers) <= 6]
    types = sorted(((-(len(_words(n) & tw) + (l in hot)), file, line, n, d, l, rev) for n, file, line, d, l, rev in conn.execute(
        "SELECT f.name, f.file, f.line, f.data, f.layer, fl.hash FROM facts f JOIN files fl ON fl.path = f.file WHERE f.unit = ? AND f.kind = 'type'", (uid,))))
    types = [t for t in types if t[0] < 0] or types[:10]  # only what the task touches; else a sample
    facts += [(f"{json.loads(d)['kind']} {n} [{l}] @{rev[:8]}", f"{file}:{line}") for _, file, line, n, d, l, rev in types[:25]]
    imps = conn.execute("SELECT name, COUNT(*) c, MIN(file || ':' || line) FROM facts WHERE unit = ? AND kind = 'import' GROUP BY name ORDER BY c DESC, name LIMIT 8", (uid,)).fetchall()
    facts += [(f"imports {m} x{c}", src) for m, c, src in imps]
    S.append(("Facts", False, facts))

    rules = [(f"[{p['status']}] {p['statement']} ({principles.predicate_text(p)})", f"principles.toml#{p['id']}")
             for p in ps if p["status"] == "observed" and principles.applies(unit, p)]
    S.append(("House rules", False, rules))

    head = f"# Packet: {uid}\nTask: {task}\ntier {tier} · budget {budget} tokens · config {cfg.hash} · index {index.digest(conn)}"

    def render(sections):
        out = [head]
        for title, _, lines in sections:
            if lines:
                out.append(f"\n## {title}\n" + "\n".join(f"- {t}  [{s}]" for t, s in lines))
        return "\n".join(out) + "\n"

    kept = [(t, m, list(l) if m else []) for t, m, l in S]
    dropped, full = [], False
    for k, (title, mand, lines) in enumerate(S):
        if mand:
            continue
        for i, line in enumerate(lines):
            trial = kept[:k] + [(title, mand, kept[k][2] + [line])] + kept[k + 1:]
            if full or est(render(trial)) > budget:
                full = True
                dropped.append({"section": title, "lines": len(lines) - i})
                break
            kept[k][2].append(line)
    md = render(kept)
    return {
        "version": VERSION, "config_hash": cfg.hash, "index_digest": index.digest(conn),
        "feature": uid, "task": task, "tier": tier, "git": _git(cfg, root),
        "budget": budget, "tokens": est(md), "over_budget": est(md) > budget, "dropped": dropped,
        "sections": [{"title": t, "tokens": est("\n".join(f"- {a}  [{b}]" for a, b in l)), "mandatory": m, "lines": len(l)} for t, m, l in kept],
        "markdown": md,
    }
