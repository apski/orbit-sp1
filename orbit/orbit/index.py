"""L2 index: one SQLite file per repo, incremental by path + content hash (spec section 5).

Index is a cache: deleting it only costs time. A schema/config/adapter mismatch forces a full rebuild.
"""
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess

from . import adapters

SCHEMA = 1
DDL = """
CREATE TABLE files(path TEXT PRIMARY KEY, hash TEXT, size INT, loc INT, lang TEXT, parse_status TEXT, mtime REAL);
CREATE TABLE units(id TEXT PRIMARY KEY, kind TEXT, name TEXT, glob TEXT, root_path TEXT);
CREATE TABLE facts(id INTEGER PRIMARY KEY, file TEXT, line INT, kind TEXT, name TEXT, unit TEXT, layer TEXT, data TEXT);
CREATE INDEX facts_file ON facts(file);
CREATE INDEX facts_kind_name ON facts(kind, name);
CREATE INDEX facts_unit_kind ON facts(unit, kind);
CREATE VIEW refs AS SELECT name AS symbol, file, line, unit FROM facts WHERE kind = 'ref';
CREATE TABLE listing(path TEXT PRIMARY KEY, kind TEXT, unit TEXT, layer TEXT);
CREATE INDEX listing_unit ON listing(unit);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def _rx(pattern):
    return re.compile(glob.translate(pattern, recursive=True, include_hidden=True))


def _prefix(pattern):
    """Static leading path of a glob: lets git skip the rest of the repo."""
    keep = []
    for part in pattern.split("/"):
        if any(c in part for c in "*?["):
            break
        keep.append(part)
    return "/".join(keep) or "."


class Plan:
    """Config compiled once: include/ignore matchers and unit definitions."""

    def __init__(self, cfg):
        ix = cfg.d["index"]
        self.include = [_rx(g) for g in ix["include"]]
        self.prefixes = sorted({_prefix(g) for g in ix["include"]})
        self.ignore = [_rx(g) for g in ix.get("ignore", [])]
        self.max_bytes = ix.get("max_bytes", 1_000_000)
        plat = cfg.d.get("platform", {})
        self.root_layer = plat.get("root_layer", "_root")
        self.exclude = [_rx(g) for g in plat.get("exclude_units", [])]
        self.unit_defs = [(u["kind"], u["glob"], len(u["glob"].split("/")), _rx(u["glob"]), u.get("layer_root")) for u in cfg.d["units"]]

    def wanted(self, rel):
        return any(r.match(rel) for r in self.include) and not any(r.match(rel) for r in self.ignore)

    def unit_of(self, rel):
        """-> (unit_id, kind, name, root, layer, glob) or None. First matching unit def wins.
        `layer_root` (e.g. "src"): layers are the directories inside it; other top-level dirs stay layers."""
        parts = rel.split("/")
        for kind, g, n, rx, layer_root in self.unit_defs:
            if len(parts) > n:
                root = "/".join(parts[:n])
                if rx.match(root) and not any(e.match(root) for e in self.exclude):
                    rest = parts[n:]
                    if layer_root and rest[0] == layer_root and len(rest) > 1:
                        rest = rest[1:]
                    return f"{kind}:{parts[n - 1]}", kind, parts[n - 1], root, (rest[0] if len(rest) > 1 else self.root_layer), g
        return None


def discover(cfg, plan):
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z", "--", *plan.prefixes],
                         cwd=cfg.root, capture_output=True, check=True).stdout
    found = {}
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode()
        if rel in found or not plan.wanted(rel):
            continue
        try:  # git -c lists tracked files deleted from the worktree; skip them
            st = os.stat(cfg.root / rel)
        except OSError:
            continue
        found[rel] = st
    return found


def _open(cfg):
    db = cfg.own(cfg.d["index"]["db"])
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    want = {"schema": str(SCHEMA), "config_hash": cfg.hash, "adapter": adapters.ADAPTER_VERSION}
    try:
        have = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('schema','config_hash','adapter')"))
    except sqlite3.OperationalError:
        have = {}
    if have != want:  # never a silent migration
        conn.close()
        db.unlink(missing_ok=True)
        conn = sqlite3.connect(db)
        conn.executescript(DDL)
        conn.executemany("INSERT INTO meta VALUES (?, ?)", want.items())
        conn.commit()
    return conn


def ensure(cfg, force=False):
    """Bring the index up to date. -> (conn, stats). Warm run: stat only, hash only when size/mtime moved."""
    if force:
        cfg.own(cfg.d["index"]["db"]).unlink(missing_ok=True)
    conn = _open(cfg)
    plan = Plan(cfg)
    langs = adapters.compile_langs(cfg)
    found = discover(cfg, plan)
    known = {r[0]: r[1:] for r in conn.execute("SELECT path, hash, size, mtime FROM files")}
    parsed, changed = 0, False
    with conn:
        for gone in set(known) - set(found):
            changed = True
            conn.execute("DELETE FROM files WHERE path=?", (gone,))
            conn.execute("DELETE FROM facts WHERE file=?", (gone,))
        conn.execute("DELETE FROM units")
        conn.execute("DELETE FROM listing")
        units = {}
        for rel in sorted(found):
            st = found[rel]
            u = plan.unit_of(rel)
            if u:
                units[u[0]] = (u[0], u[1], u[2], u[5], u[3])
            lang = adapters.lang_for(rel, langs)
            conn.execute("INSERT INTO listing VALUES (?,?,?,?)",
                         (rel, lang["name"] if lang else os.path.splitext(rel)[1].lstrip(".") or "file", u[0] if u else None, u[4] if u else None))
            old = known.get(rel)
            if old and old[1] == st.st_size and old[2] == st.st_mtime:
                continue
            data = (cfg.root / rel).read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:16]
            if old and old[0] == digest:  # touched, not changed: no re-parse
                conn.execute("UPDATE files SET mtime=?, size=? WHERE path=?", (st.st_mtime, st.st_size, rel))
                continue
            status, loc, facts = "skipped", data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0), []
            if lang and len(data) <= plan.max_bytes:
                try:
                    text, status = data.decode(), "ok"
                except UnicodeDecodeError:
                    text, status = data.decode(errors="replace"), "partial"
                facts = adapters.parse(lang, text)
                parsed += 1
            changed = True
            conn.execute("DELETE FROM facts WHERE file=?", (rel,))
            conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)",
                         (rel, digest, st.st_size, loc, lang["name"] if lang else None, status, st.st_mtime))
            conn.executemany("INSERT INTO facts(file,line,kind,name,unit,layer,data) VALUES (?,?,?,?,?,?,?)",
                             [(rel, ln, k, name, u[0] if u else None, u[4] if u else None, json.dumps(d, sort_keys=True)) for k, name, ln, d in facts])
        conn.executemany("INSERT INTO units VALUES (?,?,?,?,?)", sorted(units.values()))
        # facts carry unit/layer: refresh when unit rules changed is covered by config_hash rebuild
        if changed or not conn.execute("SELECT 1 FROM meta WHERE key='digest'").fetchone():
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('digest', ?)", (_digest(conn),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at', datetime('now'))")
    return conn, {"parsed": parsed, "files": len(found)}


def digest(conn):
    return conn.execute("SELECT value FROM meta WHERE key='digest'").fetchone()[0]


def _digest(conn):
    """Content digest of the index: no ids, mtimes or clocks, so it is identical across machines."""
    h = hashlib.sha256()
    for row in conn.execute("SELECT path, hash FROM files ORDER BY path"):
        h.update(("F|%s|%s\n" % row).encode())
    for row in conn.execute("SELECT file, line, kind, name, unit, layer, data FROM facts ORDER BY file, line, kind, name, data"):
        h.update(("T|%s|%s|%s|%s|%s|%s|%s\n" % row).encode())
    for row in conn.execute("SELECT id, root_path FROM units ORDER BY id"):
        h.update(("U|%s|%s\n" % row).encode())
    return h.hexdigest()[:16]


class UnitError(Exception):
    pass


def resolve_unit(conn, token):
    """Accept 'kind:name' or a bare name when unambiguous."""
    rows = conn.execute("SELECT id, kind, name, root_path FROM units WHERE id=? OR name=? ORDER BY id", (token, token)).fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        close = [r[0] for r in conn.execute("SELECT id FROM units WHERE id LIKE ? ORDER BY id LIMIT 5", (f"%{token}%",))]
        raise UnitError(f"unknown unit {token!r}" + (f"; did you mean: {', '.join(close)}" if close else ""))
    raise UnitError(f"ambiguous unit {token!r}: {', '.join(r[0] for r in rows)}")
