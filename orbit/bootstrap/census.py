#!/usr/bin/env python3
"""Orbit bootstrap, phase 0 (SPEC section 11): census a repo, compare it to Orbit, propose what to add.

  census.py [census] [--json]   what is in the repo: languages, target roots, signals, churn, skills, tools
  census.py propose [--json]    gap vs Orbit (orbit.toml + catalog.toml + repo policy), ranked
  census.py report              census + proposal as one markdown document

Deterministic (SPEC 10): same tree, HEAD, catalog, policy, skill roots and PATH give byte-identical output.
No clock (the churn window is anchored to the HEAD commit date), no network, no model. git is read-only.
Skills and tools depend on the machine; everything else depends only on the tree.
Stdlib only, Python >= 3.13 (glob.translate). Schema: .agents/skills/orbit-bootstrap/references/schema.md
"""
import argparse
import glob
import importlib.util
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS_SCHEMA = "orbit-census/2"
PROPOSAL_SCHEMA = "orbit-proposal/2"
KIND_ORDER = ["include", "unit", "language", "refs", "spec", "adapter", "check", "source", "tool", "skill", "skill-ref", "retire"]
# A path a skill names: optional ~ or /Users|/home prefix, at least one slash, and a file extension.
PATH_TOKEN = re.compile(r"(?<![\w./~@-])((?:~|/(?:Users|home))?/?(?:[\w@.+-]+/)+[\w@.+-]+\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/])")
BARE_TOKEN = re.compile(r"`([\w+-]+\.(?:swift|tsx?|jsx?|py|rs|kt|m|go|rb))`")
CODE_SPANS = re.compile(r"```.*?```|`[^`\n]+`", re.S)


def rx(pattern):
    return re.compile(glob.translate(pattern, recursive=True, include_hidden=True))


def git(repo, *args):
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=repo, capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape")


def under(path, root):
    return root == "." or path == root or path.startswith(root + "/")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --- inputs ---------------------------------------------------------------------------------------

def list_files(repo, exclude):
    """git ls-files: tracked + untracked, not ignored, regular files only (symlinks skipped). -> {rel: size}"""
    files = {}
    for rel in git(repo, "ls-files", "-co", "--exclude-standard", "-z").split("\0"):
        if not rel or rel in files or any(r.match(rel) for r in exclude):
            continue
        try:
            st = os.lstat(repo / rel)
        except OSError:  # tracked but deleted from the worktree
            continue
        if stat.S_ISREG(st.st_mode):
            files[rel] = st.st_size
    return dict(sorted(files.items()))


def read_text(path, limit):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def package_deps(text):
    try:
        d = json.loads(text)
    except ValueError:
        return set()
    return {k for sec in ("dependencies", "devDependencies", "peerDependencies") for k in (d.get(sec) or {})} if isinstance(d, dict) else set()


def churn_log(repo, since, exclude):
    """-> [set(paths)] one per commit since `since` (committer date), newest first; excluded paths dropped."""
    commits, cur = [], None
    for line in git(repo, "log", f"--since={since}", "--no-renames", "--name-only", "--format=commit %H").splitlines():
        if line.startswith("commit ") and len(line) == 47:
            cur = set()
            commits.append(cur)
        elif line and cur is not None and not any(r.match(line) for r in exclude):
            cur.add(line)
    return [c for c in commits if c]


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            fm[key] = "" if val in (">", "|", ">-", "|-") else val.strip("\"'")
        elif key and line[:1] in (" ", "\t"):
            fm[key] = (fm[key] + " " + line.strip()).strip()
    return fm


def missing_refs(text, md, repo, tops, basenames, in_repo):
    """Paths a skill names that do not exist. Home paths (~, /Users/..) are checked for every skill; repo paths
    (first component is a repo top-level dir) and bare `File.swift` names only for skills that live in the repo.
    ponytail: /tmp, /etc and remote-box paths are skipped (runtime outputs), and so are templated tokens."""
    home, out = str(Path.home()), set()
    for m in PATH_TOKEN.finditer(text):
        s = m.group(1)
        if any(c in s for c in "*{}<>$") or "..." in s or re.search(r"X{2,}|YYYY|example|MyApp|your[-_]", s, re.I):
            continue
        if s.startswith(("~", "/Users/", "/home/")):
            if not Path(os.path.expanduser(s)).exists():
                out.add("~" + s[len(home):] if s.startswith(home) else s)
        elif in_repo and s.split("/")[0] in tops and not (repo / s).exists() and not (md.parent / s).exists():
            out.add(s)
    if in_repo:
        out |= {m.group(1) for m in BARE_TOKEN.finditer(text) if m.group(1) not in basenames and not any(md.parent.rglob(m.group(1)))}
    return sorted(out)


def scan_skills(repo, roots, files, tool_names):
    """Frontmatter + named paths + CLI tools used in code spans. Roots keep their given label (~ unexpanded)."""
    tops = {f.split("/")[0] for f in files if "/" in f}
    basenames = {posixpath.basename(f) for f in files}
    tool_rx = {t: re.compile(r"(?<![\w./-])" + re.escape(t) + r"(?![\w-])") for t in tool_names}
    found, root_rows = {}, []
    for label in roots:
        p = Path(os.path.expanduser(label))
        p = p if p.is_absolute() else repo / p
        mds = sorted(set(p.glob("*/SKILL.md")) | set(p.glob("*/*/SKILL.md"))) if p.is_dir() else []
        names = set()
        for md in mds:
            text = read_text(md, 200_000)
            fm = frontmatter(text)
            name = fm.get("name") or md.parent.name
            names.add(name)
            if name not in found:  # first root wins for content
                in_repo = p.resolve().is_relative_to(repo.resolve())
                code = " ".join(CODE_SPANS.findall(text))
                found[name] = {"name": name, "roots": [], "description": fm.get("description", "")[:120],
                               "path": f"{label}/{md.parent.relative_to(p).as_posix()}/SKILL.md",
                               "missing_refs": missing_refs(text, md, repo, tops, basenames, in_repo),
                               "tools": sorted(t for t, r in tool_rx.items() if r.search(code))}
            if label not in found[name]["roots"]:
                found[name]["roots"].append(label)
        root_rows.append({"root": label, "exists": p.is_dir(), "skills": len(names)})
    return root_rows, [found[k] for k in sorted(found)]


def load_policy(repo, cat, path=None):
    """Per-repo product decisions (retire paths, churn window, demand buckets). Data, never code paths in core."""
    if path is None:
        path = next((repo / p for p in cat["census"].get("policy_files", []) if (repo / p).is_file()), None)
    if path is None or not Path(path).is_file():
        return {"file": None, "retire": [], "why": {}, "since": None, "demand": {}}
    path = Path(path).resolve()
    d = tomllib.loads(path.read_text()).get("policy", {})
    demand = {}
    if d.get("demand_module"):  # reuse a repo's demand script: its PATH dict of bucket -> regex
        spec = importlib.util.spec_from_file_location("orbit_demand", repo / d["demand_module"])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        demand.update(getattr(mod, "PATH", {}))
    demand.update(d.get("demand", {}))
    return {"file": path.relative_to(repo.resolve()).as_posix(), "retire": sorted(d.get("retire", [])), "why": d.get("why", {}),
            "since": d.get("since"), "demand_module": d.get("demand_module"), "demand": dict(sorted(demand.items()))}


# --- census ---------------------------------------------------------------------------------------

def load_catalog(path):
    cat = tomllib.loads(Path(path).read_text())
    cat["_ext"] = {e: name for name, l in cat["languages"].items() for e in l["ext"]}
    return cat


def lang_of(rel, cat):
    return cat["_ext"].get(posixpath.splitext(rel)[1])


def is_code(lang, cat):
    return bool(lang) and bool(cat["languages"][lang].get("code"))


def owner_fn(roots):
    """Deepest target root containing a path; a root at "." is transparent (owner = top-level dir)."""
    rs = set(roots) - {"."}

    def owner(rel):
        d = posixpath.dirname(rel)
        while d:
            if d in rs:
                return d
            d = posixpath.dirname(d)
        return rel.split("/")[0] if "/" in rel else "."
    return owner


def census(repo, cat, since=None, exclude=(), skill_roots=None, policy=None):
    c = cat["census"]
    policy = policy or {"retire": [], "demand": {}, "since": None}
    excl = [rx(g) for g in [*c.get("exclude", []), *exclude]]
    files = list_files(repo, excl)
    head = git(repo, "log", "-1", "--format=%H %cs").split() if git(repo, "rev-list", "--all", "-n1").strip() else []
    since = since or policy.get("since")
    if since is None and head:
        since = (date.fromisoformat(head[1]) - timedelta(days=c.get("churn_days", 90))).isoformat()
    commits = churn_log(repo, since, excl) if head else []

    lines, large = {}, 0
    for rel, size in files.items():
        if lang_of(rel, cat):
            if size > c.get("max_bytes", 1_000_000):
                large += 1
                lines[rel] = 0
            else:
                lines[rel] = read_text(repo / rel, size + 1).count("\n")

    hits = {}
    for s in cat["signal"]:
        g = [rx(p) for p in s["glob"]]
        cre = re.compile(s["contains"], re.M) if "contains" in s else None
        deps = set(s.get("json_dep", []))
        h = []
        for rel in files:
            if not any(r.match(rel) for r in g):
                continue
            if cre or deps:
                text = read_text(repo / rel, c.get("max_bytes", 1_000_000))
                if (cre and not cre.search(text)) or (deps and not deps & package_deps(text)):
                    continue
            h.append(rel)
        if h:
            hits[s["id"]] = h

    root_kinds = defaultdict(set)
    for s in cat["signal"]:
        for rel in hits.get(s["id"], []) if "root_up" in s else []:
            r = rel
            for _ in range(s["root_up"]):
                r = posixpath.dirname(r)
            root_kinds[r or "."].add(s["id"])
    owner = owner_fn(root_kinds)
    file_owner = {rel: owner(rel) for rel in files}

    signals = []
    for s in cat["signal"]:
        h = hits.get(s["id"])
        if not h:
            continue
        groups = Counter(file_owner[rel] for rel in h)
        signals.append({"id": s["id"], "category": s["category"], "domain": s.get("domain", ""), "files": len(h),
                        "groups": [{"root": k, "files": n} for k, n in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))],
                        "paths": h[:10]})

    by_lang = defaultdict(lambda: [0, 0])
    for rel, n in lines.items():
        by_lang[lang_of(rel, cat)][0] += 1
        by_lang[lang_of(rel, cat)][1] += n
    languages = [{"lang": k, "code": is_code(k, cat), "files": v[0], "lines": v[1]}
                 for k, v in sorted(by_lang.items(), key=lambda kv: (-kv[1][1], kv[0]))]

    def touch(prefix_of):
        out = Counter()
        for paths in commits:
            for k in {prefix_of(p) for p in paths}:
                out[k] += 1
        return out

    def stats(key_of):
        st = defaultdict(lambda: {"files": 0, "code_files": 0, "lines": 0, "langs": Counter()})
        for rel in files:
            row = st[key_of(rel)]
            row["files"] += 1
            lg = lang_of(rel, cat)
            if is_code(lg, cat):
                row["code_files"] += 1
                row["lines"] += lines[rel]
                row["langs"][lg] += lines[rel]
        return st

    sig_by_root = defaultdict(set)
    for s in signals:
        for g in s["groups"]:
            sig_by_root[g["root"]].add(s["id"])
    dom_rank = {d: i for i, d in enumerate(c.get("domains", []))}
    sig_dom = {s["id"]: s.get("domain", "") for s in cat["signal"]}

    def domain(root):
        ds = sorted({sig_dom[i] for i in sig_by_root.get(root, ()) if sig_dom[i] in dom_rank}, key=dom_rank.get)
        return ds[0] if ds else "other"

    owner_stats, owner_churn = stats(lambda rel: file_owner[rel]), touch(owner)
    dir_commits = Counter()  # commits per directory prefix, any file
    for paths in commits:
        dir_commits.update({"/".join(p.split("/")[:i]) for p in paths for i in range(1, p.count("/") + 1)})
    # Workspace packages: package.json "name" -> root, and Cargo path deps, so a target lists the siblings it uses.
    pkg_name, pkg_deps = {}, {}
    for r in root_kinds:
        pj = repo / r / "package.json"
        if "node-package" in root_kinds[r] and pj.is_file():
            try:
                d = json.loads(read_text(pj, 1_000_000))
            except ValueError:
                d = {}
            if isinstance(d, dict):
                if d.get("name"):
                    pkg_name[d["name"]] = r
                pkg_deps[r] = package_deps(json.dumps(d))
    targets = []
    for r in sorted(root_kinds):
        if r == ".":
            continue
        st = owner_stats.get(r, {"files": 0, "code_files": 0, "lines": 0, "langs": Counter()})
        mine = [rel for rel in files if under(rel, r)]
        t = {"root": r, "kinds": sorted(root_kinds[r]), "domain": domain(r), "files": st["files"], "code_files": st["code_files"],
             "lines": st["lines"], "langs": dict(sorted(st["langs"].items())), "commits": owner_churn.get(r, 0),
             "last_commit": git(repo, "log", "-1", "--format=%cs", "--", r).strip() or None if head else None,
             "signals": sorted(sig_by_root.get(r, ())), "retired": any(under(r, x) for x in policy["retire"])}
        schemes = sorted(posixpath.basename(p)[:-len(".xcscheme")] for p in files if p.endswith(".xcscheme") and file_owner[p] == r)
        if schemes:
            t["schemes"] = schemes
        tree = defaultdict(lambda: [0, 0])
        for rel in mine:
            parts = rel[len(r) + 1:].split("/")[:-1]
            for i in range(1, min(len(parts), 3) + 1):
                row = tree[r + "/" + "/".join(parts[:i])]
                if is_code(lang_of(rel, cat), cat):
                    row[0] += 1
                    row[1] += lines[rel]
        t["tree"] = [{"path": k, "code_files": v[0], "lines": v[1], "commits": dir_commits.get(k, 0)} for k, v in sorted(tree.items()) if v[0]]
        tb, flows = set(), Counter()
        for rel in mine:
            parts = rel.split("/")[:-1]
            if is_code(lang_of(rel, cat), cat):  # shallowest *Tests / *UITests dir below the root
                i = next((i for i in range(r.count("/") + 1, len(parts)) if parts[i].endswith("Tests")), None)
                if i is not None:
                    tb.add("/".join(parts[:i + 1]))
            k = rel.find("/.argent/flows/")
            if k >= 0 and rel.endswith((".yaml", ".yml")):
                flows[rel[:k + len("/.argent/flows")]] += 1
        t["test_bundles"] = sorted(tb)
        t["flows"] = [{"dir": k, "count": n} for k, n in sorted(flows.items())]
        deps = {pkg_name[n] for n in pkg_deps.get(r, ()) if n in pkg_name and pkg_name[n] != r}
        cargo = repo / r / "Cargo.toml"
        if "cargo" in root_kinds[r] and cargo.is_file():
            for m in re.finditer(r'path\s*=\s*"([^"]+)"', read_text(cargo, 200_000)):
                dep = posixpath.normpath(posixpath.join(r, m.group(1)))
                if dep in root_kinds and dep != r:
                    deps.add(dep)
        t["workspace_deps"] = sorted(deps)
        targets.append(t)

    top = lambda p: p.split("/")[0] if "/" in p else "."
    area_stats, area_churn = stats(top), touch(top)
    areas = [{"path": k, "files": v["files"], "code_files": v["code_files"], "lines": v["lines"], "commits": area_churn.get(k, 0)}
             for k, v in sorted(area_stats.items())]

    # Code hotspots: directories (depth <= 4) by commits that touched a code file in them.
    hot = Counter()
    for paths in commits:
        dirs = set()
        for p in paths:
            if is_code(lang_of(p, cat), cat):
                parts = p.split("/")[:-1]
                dirs |= {"/".join(parts[:i]) for i in range(1, min(len(parts), 4) + 1)}
        hot.update(dirs)
    hotspots = [{"path": k, "commits": n} for k, n in sorted(hot.items(), key=lambda kv: (-kv[1], kv[0]))[:c.get("hotspots", 25)]]

    demand = [{"bucket": k, "regex": v, "commits": sum(any(re.search(v, p) for p in paths) for paths in commits)}
              for k, v in policy["demand"].items()]

    tool_names = sorted({n for t in cat.get("tools", {}).values() for n in t["any"]})
    skill_root_rows, skills = scan_skills(repo, skill_roots if skill_roots is not None else c.get("skill_roots", []), files, tool_names)
    tools = []
    for tid, t in sorted(cat.get("tools", {}).items()):
        tools.append({"id": tid, "any": t["any"], "found": next((n for n in t["any"] if shutil.which(n)), None),
                      "always": bool(t.get("always")), "signals": sorted(s["id"] for s in cat["signal"] if tid in s.get("tools", []) and s["id"] in hits),
                      "skills": sorted(s["name"] for s in skills if set(t["any"]) & set(s["tools"]))})
    return {
        "schema": CENSUS_SCHEMA,
        "repo": {"name": repo.name, "head": head[0] if head else None, "head_date": head[1] if head else None,
                 "since": since, "commits": len(commits), "files": len(files), "large_files": large},
        "policy": {k: v for k, v in policy.items() if k != "demand"} if policy.get("file") else None,
        "languages": languages,
        "targets": targets,
        "signals": signals,
        "areas": areas,
        "hotspots": hotspots,
        "demand": demand,
        "skills": {"roots": skill_root_rows, "found": skills},
        "tools": tools,
        # Not printed: per-file owner/lines and the commit list, for propose. Stripped before output.
        "_files": {rel: {"owner": file_owner[rel], "lang": lang_of(rel, cat), "lines": lines.get(rel)} for rel in files},
        "_commits": commits,
    }


# --- gap analysis + proposal ----------------------------------------------------------------------

def load_orbit(repo, cat, path=None):
    if path is None:
        path = next((repo / p for p in cat["census"].get("orbit_configs", []) if (repo / p).is_file()), None)
    if path is None or not Path(path).is_file():
        return None
    path = Path(path).resolve()
    d = tomllib.loads(path.read_text())
    root = (path.parent / d.get("project", {}).get("root", ".")).resolve()
    prefix = root.relative_to(repo.resolve()).as_posix()
    ix = d.get("index", {})
    units = [(u["kind"], u["glob"]) for u in d.get("units", [])]
    return {
        "config": path.relative_to(repo.resolve()).as_posix(),
        "prefix": "" if prefix == "." else prefix + "/",
        "include": ix.get("include", []), "ignore": ix.get("ignore", []),
        "_inc": [rx(g) for g in ix.get("include", [])], "_ign": [rx(g) for g in ix.get("ignore", [])],
        "exts": {e: name for name, l in d.get("languages", {}).items() for e in l.get("ext", [])},
        "_langs": d.get("languages", {}),
        "units": units, "_units": [(k, g, len(g.split("/")), rx(g)) for k, g in units],
        "exclude_units": d.get("platform", {}).get("exclude_units", []),
        "checks": {k: " ".join(v.get("command", [])) for k, v in sorted(d.get("checks", {}).items())},
        "check_sets": {k: v for k, v in sorted(d.get("check_sets", {}).items())},
        "sources": sorted(d.get("sources", {})),
        "specs": sorted({s["path"] for s in d.get("spec", [])}),
    }


def covered(orbit, rel):
    if not orbit or not rel.startswith(orbit["prefix"]):
        return False
    r = rel[len(orbit["prefix"]):]
    return (posixpath.splitext(r)[1] in orbit["exts"] and any(x.match(r) for x in orbit["_inc"])
            and not any(x.match(r) for x in orbit["_ign"]))


def unit_root(orbit, rel):
    """-> (kind, root dir) of the first Orbit unit def that holds rel, else None (exclude_units ignored)."""
    if not orbit or not rel.startswith(orbit["prefix"]):
        return None
    parts = rel[len(orbit["prefix"]):].split("/")
    for kind, _, n, r in orbit["_units"]:
        if len(parts) > n and r.match("/".join(parts[:n])):
            return kind, orbit["prefix"] + "/".join(parts[:n])
    return None


def propose(cen, cat, orbit, repo, policy):
    files, commits = cen["_files"], cen["_commits"]
    # Retire roots: the policy's, plus frozen app targets (kinds in [census].freeze_kinds, 0 commits in the window).
    # A root nested under another retire root folds into it, so each retired app gets exactly one item.
    freeze = set(cat["census"].get("freeze_kinds", []))
    why_retire = {x: f"policy: {policy['why'].get(x, 'retire')}" for x in policy["retire"]}
    for t in cen["targets"]:
        if t["commits"] == 0 and freeze & set(t["kinds"]) and t["root"] not in why_retire:
            why_retire[t["root"]] = f"frozen: {', '.join(sorted(freeze & set(t['kinds'])))} target with 0 commits since {cen['repo']['since']}; confirm with a person or record it in the policy"
    retire = sorted(x for x in why_retire if not any(y != x and under(x, y) for y in why_retire))
    retired = lambda rel: any(under(rel, x) for x in retire)
    code_all = {rel: f for rel, f in files.items() if is_code(f["lang"], cat)}
    code = {rel: f for rel, f in code_all.items() if not retired(rel)}  # integrate items never cover retired paths
    has_adapter = lambda rel: bool(orbit) and posixpath.splitext(rel)[1] in orbit["exts"]
    cov = {rel: covered(orbit, rel) for rel in code_all}
    per_file = defaultdict(set)
    for i, paths in enumerate(commits):
        for p in paths:
            per_file[p].add(i)
    churn = lambda rels: len(set().union(*(per_file.get(r, set()) for r in rels))) if rels else 0
    tdom = {t["root"]: t["domain"] for t in cen["targets"]}
    tkinds = {t["root"]: set(t["kinds"]) for t in cen["targets"]}
    dom_of = lambda root: tdom.get(root, "other")
    lines_of = lambda rels: sum(files[r]["lines"] or 0 for r in rels)
    major = lambda rels: Counter(dom_of(files[r]["owner"]) for r in rels).most_common(1)[0][0]

    def ev(rels, n=3):
        c = Counter(files[r]["owner"] for r in rels)
        return [f"{k}:{v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]

    def langs_txt(rels):
        c = Counter(files[r]["lang"] for r in rels)
        return ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

    items = []

    def add(kind, ident, domain, rels, scope, why, change, evidence, churn_rels=None, gap=None):
        """score = (commits + 1) x gap ratio; gap ratio = gap / scope (the share of the item's scope it fixes)."""
        rels = sorted(rels)
        ch = churn(churn_rels if churn_rels is not None else rels)
        g = len(rels) if gap is None else gap
        items.append({"kind": kind, "id": ident, "domain": domain, "score": round((ch + 1) * g / max(scope, 1), 2), "commits": ch,
                      "gap_files": g, "scope_files": scope, "gap_lines": lines_of(rels), "why": why, "evidence": evidence, "change": change})

    # coverage per language (retired paths excluded, so applying the proposal can reach 100%)
    coverage = []
    for lg in sorted({f["lang"] for f in code.values()}):
        rels = [r for r in code if code[r]["lang"] == lg]
        cv = [r for r in rels if cov[r]]
        coverage.append({"lang": lg, "files": len(rels), "covered_files": len(cv), "lines": lines_of(rels), "covered_lines": lines_of(cv),
                         "adapter": "yes" if all(map(has_adapter, rels)) else "partial" if any(map(has_adapter, rels)) else "no"})

    # include: uncovered code with an existing language adapter, per owner root
    unc = defaultdict(list)
    for rel in code:
        if not cov[rel] and has_adapter(rel):
            unc[code[rel]["owner"]].append(rel)
    for root, rels in sorted(unc.items()):
        scope = [r for r in code if code[r]["owner"] == root]
        here = [r for r in scope if cov[r]]
        inc_here = [g for g in (orbit["include"] if orbit else []) if under(orbit["prefix"] + g, root)]
        why = f"{len(rels)} code files ({langs_txt(rels)}; {lines_of(rels)} lines) under {root}/ are outside Orbit's include"
        if here:
            why += f"; Orbit covers only {len(here)} here, via {', '.join(inc_here) or 'other globs'}"
        add("include", f"{root}/**", dom_of(root), rels, len(scope), why, f'[index] include += "{root}/**"',
            ev(rels) if root == "." else [f"{root}:{len(rels)}", *[f"covered {g}:{len(here)}" for g in inc_here[:1]]])

    # unit: (a) dirs named like feature parents whose children hold code; (b) parents of sibling roots of a
    # signal with unit_kind (Cargo crates). Never an existing unit glob, never inside an existing unit.
    exist_globs = {orbit["prefix"] + g for _, g in orbit["units"]} if orbit else set()
    parents = set(cat["census"].get("unit_parents", []))
    children, kinds = defaultdict(lambda: defaultdict(list)), {}
    for rel in code:
        parts = rel.split("/")
        for i in range(len(parts) - 2):
            if parts[i] in parents:
                children["/".join(parts[:i + 1])][parts[i + 1]].append(rel)
    for s in cat["signal"]:
        if "unit_kind" in s:
            crates = [r for r, k in tkinds.items() if s["id"] in k and not retired(r)]
            for r in crates:
                d = posixpath.dirname(r) or "."
                kinds[d] = s["unit_kind"]
                children[d][posixpath.basename(r)] += [x for x in code if under(x, r)]
    for d, kids in sorted(children.items()):
        g = f"{d}/*"
        kids = {k: v for k, v in kids.items() if v}
        if len(kids) < 2 or g in exist_globs or unit_root(orbit, d + "/_"):
            continue
        nested = sorted({"/".join(eg.split("/")[:len(g.split("/"))]) for eg in exist_globs if eg.startswith(d + "/")})
        scope = [r for k in kids.values() for r in k]
        rels = [r for r in scope if not unit_root(orbit, r)]
        if not rels:
            continue
        root = code[rels[0]]["owner"]
        kind = kinds.get(d) or slug(f"{posixpath.basename(root)}-{posixpath.basename(d)}")
        change = f'[[units]] kind = "{kind}", glob = "{g}"'
        if nested:
            change += "; [platform] exclude_units += " + ", ".join(f'"{n}"' for n in nested) + " (already a unit kind)"
        add("unit", g, major(rels), rels, len(scope), f"{len(kids)} dirs under {d}/ ({', '.join(sorted(kids)[:6])}{', ...' if len(kids) > 6 else ''}) "
            f"hold {len(rels)} code files in no Orbit unit", change, [f"{d}:{len(kids)} dirs", *ev(rels, 2)])

    # language: code files whose extension no Orbit [languages] block handles
    for c in coverage:
        rels = [r for r in code if code[r]["lang"] == c["lang"] and not has_adapter(r)]
        if lines_of(rels) < cat["census"].get("min_lines", 200):
            continue
        exts = sorted({posixpath.splitext(r)[1] for r in rels})
        owners = sorted({code[r]["owner"] for r in rels})
        common = posixpath.commonpath(owners) if len(owners) > 1 else owners[0]
        incs = [common] if common not in ("", ".") else owners[:3]
        change = (f"[languages.{c['lang']}] regex block ({', '.join(exts)}) + golden fixture" if c["adapter"] == "no"
                  else f"add {', '.join(exts)} to the ext list of the Orbit block that parses {c['lang']}")
        change += "; [index] include += " + ", ".join(f'"{i}/**"' for i in incs if i != ".")
        add("language", c["lang"], major(rels), rels, c["files"],
            f"{len(rels)} {c['lang']} files ({lines_of(rels)} lines, {', '.join(exts)}) have no Orbit language block", change, ev(rels))

    # refs: declared type names the ref regex can never match although code uses them, so `orbit refs <Name>`
    # and packets miss every use. Names nobody uses (SwiftUI *_Previews) are not a gap.
    for lname, lc in sorted((orbit or {}).get("_langs", {}).items()):
        if "type" not in lc or "ref" not in lc:
            continue
        trx, rrx, exts = re.compile(lc["type"]), re.compile(lc["ref"]), tuple(lc.get("ext", []))
        texts = {rel: read_text(repo / rel, 1_000_000) for rel in code if rel.endswith(exts)}
        decl, total = defaultdict(set), 0
        for rel, text in texts.items():
            for line in text.splitlines():
                m = trx.match(line)
                name = m and m.groupdict().get("name")
                if name and "." not in name and name[0].isupper():  # refs target TitleCase symbols
                    total += 1
                    if not rrx.fullmatch(name):
                        decl[name].add(rel)
        if not decl:
            continue
        word = re.compile(r"\b(" + "|".join(map(re.escape, sorted(decl, key=len, reverse=True))) + r")\b")
        uses, users = Counter(), defaultdict(set)
        for rel, text in texts.items():
            for m in word.finditer(text):
                uses[m.group(1)] += 1
                users[m.group(1)].add(rel)
        used = sorted((n for n in decl if uses[n] > len(decl[n])), key=lambda n: (-uses[n], n))  # more uses than declarations
        if not used:
            continue
        rels = sorted(set().union(*(users[n] for n in used)))
        top = ", ".join(f"{n} ({uses[n] - len(decl[n])} uses)" for n in used[:8])
        add("refs", lname, major(rels), rels, len(texts),
            f"{len(used)} declared {lname} types the ref regex can never match are used {sum(uses[n] - len(decl[n]) for n in used)} times: {top}"
            f"{', ...' if len(used) > 8 else ''}; refs and packets miss all of them",
            f"widen [languages.{lname}].ref, e.g. prepend \\b(?:{'|'.join(used[:5])})\\b| (or a general rule such as {lname} names >= 3 chars) + golden case",
            ev(rels))  # gap = files that use a blind name

    # spec: spec docs no orbit [[spec]] points to; matched to unit names where the file stem says so
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    unit_names = defaultdict(set)
    for rel in code_all:
        u = unit_root(orbit, rel)
        if u and not any(under(u[1], x) for x in (orbit or {}).get("exclude_units", [])):
            unit_names[norm(posixpath.basename(u[1]))].add(u)
    spec_rx = [rx(g) for g in cat["census"].get("spec_globs", [])]
    linked = set((orbit or {}).get("specs", []))
    by_dir = defaultdict(list)
    for rel in files:
        if any(r.match(rel) for r in spec_rx) and not retired(rel):
            by_dir[posixpath.dirname(rel)].append(rel)
    for d, docs in sorted(by_dir.items()):
        un = [p for p in docs if p[len((orbit or {}).get("prefix", "")):] not in linked]
        if not un:
            continue
        stem = lambda p: norm(re.sub(r"(\.spec)?\.mdx?$", "", posixpath.basename(p)))
        matches = [(p, sorted(unit_names[stem(p)])) for p in un if unit_names.get(stem(p))]
        churn_rels = [r for _, us in matches for _, root in us for r in code_all if under(r, root)]
        change = "; ".join(f'[[spec]] role = "contract", unit_kinds = ["{us[0][0]}"], units = ["{posixpath.basename(us[0][1])}"], path = "{p}"'
                           for p, us in matches[:4]) or f"link each doc to its unit with a [[spec]] entry (no unit name matched)"
        rest = [posixpath.basename(p) for p in un if p not in dict(matches)]
        add("spec", d, major(churn_rels) if churn_rels else "other", un, len(docs),
            f"{len(un)} of {len(docs)} spec docs in {d}/ are linked from no Orbit [[spec]]; {len(matches)} match a unit by name"
            + (f"; unmatched: {', '.join(rest[:6])}" if rest else ""), change, [f"{d}:{len(un)}", *[f"{posixpath.basename(p)}->{posixpath.basename(us[0][1])}" for p, us in matches[:3]]],
            churn_rels=churn_rels)

    # adapters / checks / sources / tools / skills from detected signals. A signal with a domain only counts at
    # roots of that domain (react-dom in a backend's package.json does not make it a web app); retired roots never count.
    sig = {s["id"]: s for s in cen["signals"]}
    cdom = {s["id"]: s.get("domain", "") for s in cat["signal"]}
    roots_of = lambda sids: sorted({g["root"] for i in sids for g in sig[i]["groups"]
                                    if (not cdom[i] or dom_of(g["root"]) == cdom[i]) and not retired(g["root"])})
    active = {i for i in sig if roots_of([i])}
    need = defaultdict(lambda: defaultdict(set))
    for s in cat["signal"]:
        if s["id"] in active:
            for k in ("adapters", "checks", "sources", "skills", "tools"):
                for x in s.get(k, []):
                    need[k][x].add(s["id"])
    in_roots = lambda roots: [r for r in code if code[r]["owner"] in roots]
    dom_rank = {d: i for i, d in enumerate(cat["census"].get("domains", []))}

    def best_dom(sids, rels):
        ds = sorted({cdom[i] for i in sids if cdom[i] in dom_rank}, key=dom_rank.get)
        return ds[0] if ds else major(rels) if rels else "other"

    def wanted(sids):
        return ", ".join(sorted(sids))

    for a, sids in sorted(need["adapters"].items()):
        spec = cat["adapters"][a]
        roots = roots_of(sids)
        rels = [r for r in in_roots(roots) if code[r]["lang"] in spec["languages"]]
        gap = [r for r in rels if not cov[r]]
        if not rels or len(gap) <= 0.1 * len(rels):  # present: >= 90% covered
            continue
        status = "partial" if len(gap) < len(rels) else "missing"
        add("adapter", a, best_dom(sids, gap), gap, len(rels), f"{status}: {wanted(sids)} detected; Orbit covers {len(rels) - len(gap)}/{len(rels)} "
            f"{'/'.join(spec['languages'])} files in {', '.join(roots[:3])}{' (+' + str(len(roots) - 3) + ')' if len(roots) > 3 else ''}", spec["build"], ev(gap))

    cmds = orbit["checks"].values() if orbit else []
    for ck, sids in sorted(need["checks"].items()):
        spec = cat["checks"][ck]
        m = re.compile(spec["match"])
        for root in roots_of(sids):
            # ponytail: "names the root" is a substring test; a root named like part of another path can false-match.
            rels = in_roots([root])
            if not rels or any(m.search(c) and root in c for c in cmds):
                continue
            here = sorted(i for i in sids if root in {g["root"] for g in sig[i]["groups"]})
            ws = root  # {workspace}: outermost ancestor root of the same signal kind (a Cargo workspace), else the root
            for anc in sorted(tkinds, key=len):
                if anc != root and under(root, anc) and tkinds[anc] & tkinds.get(root, set()) & sids:
                    ws = anc
                    break
            cid = f"{ck}-{slug(posixpath.basename(root))}"
            cmd = spec["command"].replace("{root}", root).replace("{workspace}", ws).replace("{name}", posixpath.basename(root))
            add("check", f"{ck}:{root}", dom_of(root), rels, len(rels), f"{wanted(here)} at {root}; no Orbit check runs {ck} there",
                f'[checks.{cid}] command = {cmd}; [check_sets] {dom_of(root)} += ["{cid}"]'
                + ("" if dom_of(root) in (orbit or {}).get("check_sets", {}) else f" (Orbit has no `{dom_of(root)}` check-set yet)"),
                [f"{root}:{len(rels)}"])

    for src, sids in sorted(need["sources"].items()):
        rels = in_roots(roots_of(sids))
        if rels and not (orbit and src in orbit["sources"]):
            add("source", src, best_dom(sids, rels), rels, len(rels), f"{wanted(sids)} detected; orbit.toml has no [sources.{src}]",
                cat["sources"][src]["build"], ev(rels))

    for t in cen["tools"]:
        if t["found"]:
            continue
        sids = need["tools"].get(t["id"], set())
        if not (t["always"] or sids or t["skills"]):
            continue
        rels = in_roots(roots_of(sids)) if sids and not t["always"] else list(code)
        reasons = (["every agent needs it"] if t["always"] else []) + ([f"wanted by {wanted(sids)}"] if sids else []) \
            + ([f"{len(t['skills'])} skills call it ({', '.join(t['skills'][:4])})"] if t["skills"] else [])
        add("tool", t["id"], best_dom(sids, rels) if sids else "tooling", rels, len(rels),
            f"MISSING on PATH: none of {', '.join(t['any'])}; {'; '.join(reasons)}", cat["tools"][t["id"]].get("install", f"install one of {', '.join(t['any'])}"),
            [f"{s}" for s in t["skills"][:3]] or ev(rels), churn_rels=rels if (sids or t["always"]) else [])

    have = {s["name"] for s in cen["skills"]["found"]}
    for sk, sids in sorted(need["skills"].items()):
        rels = in_roots(roots_of(sids))
        if rels and sk not in have:
            add("skill", sk, best_dom(sids, rels), rels, len(rels), f"MISSING: wanted by {wanted(sids)}; not found in any skill root",
                cat.get("skills", {}).get(sk, f"build skill {sk}"), ev(rels))

    # skill-ref: skills that name files which do not exist (stale guidance an agent will follow)
    words = cat["census"].get("domain_words", {})
    for s in cen["skills"]["found"]:
        if not s["missing_refs"]:
            continue
        text = f"{s['name']} {s['description']}".lower()
        dom = next((d for d in cat["census"].get("domains", []) if any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words.get(d, []))), "other")
        md = s["path"] if not s["path"].startswith(("~", "/")) else None
        add("skill-ref", s["name"], dom, [], len(s["missing_refs"]), f"{s['path']} names {len(s['missing_refs'])} path(s) that do not exist",
            "fix or delete the references: " + ", ".join(s["missing_refs"][:4]) + (", ..." if len(s["missing_refs"]) > 4 else ""),
            s["missing_refs"][:3], churn_rels=[md] if md else [], gap=len(s["missing_refs"]))

    # retire: the area goes away (policy) or has gone quiet (frozen). Inventory it for removal instead of integrating it.
    texts = None
    tby = {t["root"]: t for t in cen["targets"]}
    id_rx = re.compile(r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*"?([\w.-]+)"?;|applicationId\s*=?\s*"([\w.]+)"|"(?:bundleIdentifier|package)"\s*:\s*"([\w.]+)"')
    id_files = [r for r in files if r.endswith((".pbxproj", ".gradle", "app.json", "app.config.json"))]
    ids_cache = {}

    def ids_of(rel):
        if rel not in ids_cache:
            ids_cache[rel] = {g for m in id_rx.finditer(read_text(repo / rel, 2_000_000)) for g in m.groups() if g and "$(" not in g} \
                if rel in id_files else set()
        return ids_cache[rel]

    for path in retire:
        rels = [r for r in code_all if under(r, path)]
        allf = [r for r in files if under(r, path)]
        if not allf:
            continue
        if texts is None:  # one pass over small text files outside retired paths, for inbound references
            texts = {r: read_text(repo / r, 300_000) for r in files if not retired(r) and files[r]["lang"] and (files[r]["lines"] or 0) > 0}
        needles = [path] + (["/".join(path.split("/")[-2:])] if path.count("/") >= 2 else [])
        inbound = sorted(r for r, t in texts.items() if any(n in t for n in needles))
        last = git(repo, "log", "-1", "--format=%cs", "--", path).strip() or "never"
        # External ids to clean up outside the repo (App Store / Play / Sentry): bundle ids and packages.
        ext = sorted({i for r in allf for i in ids_of(r)})
        ext_users = {i: sorted({files[r]["owner"] for r in id_files if not retired(r) and i in ids_of(r)}) for i in ext}
        # Sibling packages it uses: keep (and prune) the ones another live target also uses.
        wdeps = sorted({d for x in why_retire if under(x, path) for d in tby.get(x, {}).get("workspace_deps", []) if not retired(d)})
        users = {d: sorted(t["root"] for t in cen["targets"] if d in t["workspace_deps"] and not retired(t["root"])) for d in wdeps}
        nested = sorted(r for r in why_retire if r != path and under(r, path))
        inv = {"root": path, "files": len(allf), "code_files": len(rels), "lines": lines_of(rels), "last_commit": last,
               "workspace_deps": [{"root": d, "also_used_by": users[d], "action": "keep, prune" if users[d] else "remove"} for d in wdeps],
               "external_ids": [{"id": i, "also_used_by": ext_users[i], "action": "keep (live target uses it)" if ext_users[i] else "retire"} for i in ext],
               "inbound_refs": inbound, "nested_targets": nested}
        add("retire", path, major(rels) if rels else dom_of(path), rels, len(rels) or 1,
            f"{why_retire[path]}. {len(allf)} files ({len(rels)} code, {lines_of(rels)} lines), last commit {last}; {len(inbound)} files outside it mention it"
            + (f"; uses {', '.join(f'{d} ({w['action']})' for d, w in zip(wdeps, inv['workspace_deps']))}" if wdeps else "")
            + (f"; external ids {', '.join(i + (' (shared with ' + ', '.join(ext_users[i]) + ': keep)' if ext_users[i] else '') for i in ext[:4])}" if ext else "")
            + f". No integrate item covers its {len(rels)} code files",
            f"inventory then remove {path}/ and update the {len(inbound)} referencing files; do not index it or add checks for it",
            [f"{path}:{len(allf)} files", *[f"ref {r}" for r in inbound[:4]]])
        items[-1]["inventory"] = inv

    items.sort(key=lambda i: (-i["score"], -i["gap_files"], KIND_ORDER.index(i["kind"]), i["id"]))
    for n, i in enumerate(items, 1):
        i["rank"] = n

    routing = []
    for t, spec in sorted(cat.get("tasks", {}).items()):
        on = [w for w in spec["when"] if w in active]
        if spec["when"] and not on:
            continue
        routing.append({"task": t, "signals": on, "skills": [{"name": s, "present": s in have} for s in spec["skills"]]})

    o = {k: v for k, v in orbit.items() if not k.startswith("_") and k not in ("exts", "prefix")} if orbit else None
    if o:
        o["languages"] = sorted(set(orbit["exts"].values()))
        o["units"] = [{"kind": k, "glob": g} for k, g in orbit["units"]]
    return {"schema": PROPOSAL_SCHEMA, "repo": cen["repo"], "policy": cen["policy"], "orbit": o, "coverage": coverage,
            "items": items, "routing": routing}


# --- rendering ------------------------------------------------------------------------------------

def public(cen):
    return {k: v for k, v in cen.items() if not k.startswith("_")}


def md_census(cen):
    r = cen["repo"]
    out = [f"## Census: `{r['name']}` @ `{(r['head'] or 'no commits')[:10]}` ({r['head_date']}), churn since {r['since']} ({r['commits']} commits)", "",
           f"{r['files']} files after excludes."]
    if cen["policy"]:
        p = cen["policy"]
        out += ["", f"Policy `{p['file']}`: retire {', '.join(f'`{x}`' for x in p['retire']) or 'nothing'}"
                + (f"; window since {p['since']}" if p.get("since") else "") + (f"; demand buckets from `{p['demand_module']}`" if p.get("demand_module") else "") + "."]
    out += ["", "### Languages", "", "| language | code | files | lines |", "|---|---|---:|---:|"]
    out += [f"| {l['lang']} | {'yes' if l['code'] else ''} | {l['files']} | {l['lines']} |" for l in cen["languages"]]
    out += ["", "### Target roots", "", "| root | kinds | domain | code files | lines | commits | last commit | signals |", "|---|---|---|---:|---:|---:|---|---|"]
    out += [f"| `{t['root']}`{' (retire)' if t['retired'] else ''} | {', '.join(t['kinds'])} | {t['domain']} | {t['code_files']} | {t['lines']} | {t['commits']} | {t['last_commit']} | "
            f"{', '.join(s for s in t['signals'] if s not in t['kinds'])}{' · schemes: ' + ', '.join(t['schemes']) if 'schemes' in t else ''}"
            f"{' · test bundles: ' + ', '.join(b[len(t['root']) + 1:] for b in t['test_bundles']) if t['test_bundles'] else ''}"
            f"{' · flows: ' + ', '.join(f['dir'][len(t['root']) + 1:] + ':' + str(f['count']) for f in t['flows']) if t['flows'] else ''}"
            f"{' · uses: ' + ', '.join(t['workspace_deps']) if t['workspace_deps'] else ''} |"
            for t in cen["targets"] if t["code_files"] or t["kinds"] != ["node-package"]]
    out += ["", "### Signals", ""]
    for cat in ["platform", "framework", "build", "test", "ci", "design", "agent-context"]:
        rows = [s for s in cen["signals"] if s["category"] == cat]
        if rows:
            out.append(f"- **{cat}**: " + "; ".join(f"`{s['id']}` {s['files']} ({', '.join(g['root'] + ':' + str(g['files']) for g in s['groups'][:3])})" for s in rows))
    ctx = [p for s in cen["signals"] if s["category"] == "agent-context" for p in s["paths"]]
    stale = [s for s in cen["skills"]["found"] if s["missing_refs"]]
    out += ["", "### Agent context", "", f"Docs: {', '.join(f'`{p}`' for p in ctx[:12]) or 'none'}.", "",
            "Skill roots: " + "; ".join(f"`{x['root']}` {x['skills'] if x['exists'] else 'absent'}" for x in cen["skills"]["roots"])
            + f" ({len(cen['skills']['found'])} distinct skills; {len(stale)} name paths that do not exist).", "",
            "Tools: " + "; ".join(f"{t['id']} {'`' + t['found'] + '`' if t['found'] else '**MISSING**'}" for t in cen["tools"]) + ".",
            "", "### Churn by top-level area", "", "| area | commits | code files | lines |", "|---|---:|---:|---:|"]
    out += [f"| `{a['path']}` | {a['commits']} | {a['code_files']} | {a['lines']} |" for a in sorted(cen["areas"], key=lambda a: (-a["commits"], a["path"]))[:15]]
    out += ["", "### Code hotspots (commits touching code in the dir)", "", ", ".join(f"`{h['path']}` {h['commits']}" for h in cen["hotspots"])]
    if cen["demand"]:
        out += ["", "### Demand buckets (policy)", "", "| bucket | commits |", "|---|---:|"] + [f"| {d['bucket']} | {d['commits']} |" for d in cen["demand"]]
    return "\n".join(out) + "\n"


def md_item(i):
    return (f"{i['rank']}. **{i['kind']}** `{i['id']}` · {i['domain']} · score {i['score']} (commits {i['commits']}, gap {i['gap_files']}/{i['scope_files']})\n"
            f"   - why: {i['why']}. Evidence: {', '.join(i['evidence'])}\n   - change: {i['change']}")


def md_propose(p, domain=None, top=None):
    out = ["## Orbit coverage", "", f"Config: `{p['orbit']['config']}`" if p["orbit"] else "No orbit.toml found: everything is a gap.", "",
           "| language | files | covered | lines | covered lines | Orbit adapter |", "|---|---:|---:|---:|---:|---|"]
    out += [f"| {c['lang']} | {c['files']} | {c['covered_files']} | {c['lines']} | {c['covered_lines']} | {c['adapter']} |" for c in p["coverage"]]
    items = [i for i in p["items"] if not domain or i["domain"] == domain]
    out += ["", "## Proposal", "", "Ranked by score = (commits since the window start + 1) x gap / scope: churn times the share of the item's scope it fixes. "
            "Nothing here is applied; a person approves each item.", ""]
    if not domain:
        mob = [i for i in items if i["domain"] == "mobile"]
        out += [f"### Mobile ({len(mob)} items)", "", *map(md_item, mob[:top]), ""]
        out += [f"### All domains ({len(items)} items{', top ' + str(top) if top and top < len(items) else ''})", ""]
    out += [*map(md_item, items[:top]), "", "## Routing table (task -> skills)", "", "| task | signals | skills |", "|---|---|---|"]
    out += [f"| {r['task']} | {', '.join(r['signals']) or 'always'} | {', '.join(s['name'] if s['present'] else s['name'] + ' (MISSING)' for s in r['skills'])} |" for r in p["routing"]]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", default="census", choices=["census", "propose", "report"])
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    ap.add_argument("--catalog", default=str(HERE / "catalog.toml"))
    ap.add_argument("--orbit-config", help="orbit.toml (default: first of catalog [census].orbit_configs)")
    ap.add_argument("--policy", help="repo policy TOML (default: first of catalog [census].policy_files)")
    ap.add_argument("--since", help="churn window start, YYYY-MM-DD (default: policy since, else HEAD date - churn_days)")
    ap.add_argument("--exclude", action="append", default=[], help="extra repo-relative glob to skip (repeatable)")
    ap.add_argument("--skill-root", action="append", help="replace catalog skill_roots (repeatable; repo-relative or ~)")
    ap.add_argument("--domain", help="propose: only this domain (mobile, web, backend, ...)")
    ap.add_argument("--top", type=int, help="propose/report: at most N items per list")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    cat = load_catalog(a.catalog)
    policy = load_policy(repo, cat, a.policy and Path(a.policy))
    cen = census(repo, cat, a.since, a.exclude, a.skill_root, policy)
    if a.cmd == "census":
        print(json.dumps(public(cen), indent=2, sort_keys=True, ensure_ascii=False) if a.json else md_census(cen), end="\n" if a.json else "")
        return 0
    p = propose(cen, cat, load_orbit(repo, cat, a.orbit_config and Path(a.orbit_config)), repo, policy)
    if a.domain:
        p["items"] = [i for i in p["items"] if i["domain"] == a.domain]
    if a.json:
        print(json.dumps(p, indent=2, sort_keys=True, ensure_ascii=False))
    elif a.cmd == "propose":
        print(md_propose(p, a.domain, a.top), end="")
    else:
        print(md_census(cen) + "\n" + md_propose(p, a.domain, a.top), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
