"""Router (spec 8.2): score routes by words (strong words count double). Deterministic, no model. Ties go to the earlier route.

`exact` words score like strong ones but match the raw token, before plural stripping: "implements" (who conforms)
is not "implement" (build something). A commit-style task ("fix(ios): ...") scores 10 for the routes listing its
type in `headers`: the header states the kind of task outright, so it outweighs the words.

{unit} for an edit task (packet routes) comes from the code: the unit whose files best match the task words
(`rank_files`, BM25 over file text). A platform word in the task ("ios", "web", "server") narrows the unit kinds."""
import math
import re
from collections import Counter

from . import VERSION


STOP = set("what is the a an of in to for do we how does are and or on it this that with from by be".split())


def _norm(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokens(text):
    return [_norm(w) for w in re.findall(r"[a-z0-9_]+", text.lower())]


HEADER = re.compile(r"\s*([a-z]+)(?:\([^)]*\))?!?:\s", re.I)  # "fix(ios): ...", "feat: ..."


def route(cfg, question):
    data, _ = cfg.load_own("routes")
    thr = data["router"]["threshold"]
    raw = set(re.findall(r"[a-z0-9_]+", question.lower()))
    qt = {_norm(w) for w in raw}
    head = HEADER.match(question)
    best, score = None, 0
    for r in data["routes"]:
        s = len(qt & {_norm(w) for w in r["words"]}) + 2 * len(qt & {_norm(w) for w in r.get("strong", [])}) + 3 * len(raw & set(r.get("exact", [])))
        s += 10 * bool(head and head[1].lower() in r.get("headers", []))
        if s > score:
            best, score = r, s
    return (best if score >= thr else None), score, data


def _unit_in(question, conn, exact=False, kinds=()):
    """A unit named in the question. `exact`: spelled with the unit's own case, from `kinds` when given."""
    q = question if exact else question.lower()
    names = [n for n, k in conn.execute("SELECT name, kind FROM units ORDER BY name") if not kinds or k in kinds]
    # "tax" is py-service:tax, "Tax" the iOS screen folder: a name spelled with the question's case goes first
    for name in sorted(set(names), key=lambda n: (-len(n), n not in question, n)):
        for form in {name, name.replace("-", " "), name.replace("_", " ")}:
            if re.search(rf"(?<![\w-]){re.escape(form if exact else form.lower())}(?![\w-])", q):
                return name
    return None


def _stem(w):
    w = w.lower()
    for suf in ("ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def code_words(text):
    """camelCase / snake_case words, stemmed: 'OrderImportingScreen' -> order, import, screen."""
    return [_stem(w) for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", text) if w.lower() not in STOP]


def platform_kinds(conn, words):
    """Unit kinds whose platform prefix ("ios" in ios-next) is one of `words`; empty means every kind."""
    return {k for (k,) in conn.execute("SELECT DISTINCT kind FROM units") if k.split("-")[0] in words}


def rank_files(cfg, conn, task, kinds=()):
    """-> [(score, path, unit)] best first: BM25 of the task's words over each unit file's path and text.
    ponytail: reads the candidate files per call (~0.2 s for all iOS); store word bags in the index if it gets slow."""
    tw = set(code_words(task))
    if not tw:
        return []
    bags = {}
    for path, unit, kind in conn.execute("SELECT l.path, l.unit, u.kind FROM listing l JOIN units u ON u.id = l.unit "
                                         "JOIN files f ON f.path = l.path WHERE f.lang IS NOT NULL ORDER BY l.path"):
        if kinds and kind not in kinds:
            continue
        try:
            text = (cfg.root / path).read_text(errors="replace")
        except OSError:
            continue
        bags[path] = (unit, Counter(code_words(path.split("/", 2)[-1].rsplit(".", 1)[0] + " " + text)))
    if not bags:
        return []
    df = Counter(w for _, b in bags.values() for w in tw & b.keys())
    n, avg = len(bags), sum(b.total() for _, b in bags.values()) / len(bags)
    idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
    out = []
    for path, (unit, b) in bags.items():
        norm = 1.2 * (0.25 + 0.75 * b.total() / avg)
        s = sum(idf[w] * b[w] * 2.2 / (b[w] + norm) for w in tw if w in b)
        if s > 0:
            out.append((round(s, 6), path, unit))
    return sorted(out, key=lambda t: (-t[0], t[1]))


def _unit_by_files(cfg, conn, question, k=3):
    """A unit the task names (exact case, on the task's platform), else the unit holding the most score among the
    k files that best match the task. Lowercase words that happen to be unit names ("account", "tax") are task words."""
    kinds = platform_kinds(conn, set(re.findall(r"[a-z]+", question.lower())))
    named = _unit_in(question, conn, exact=True, kinds=kinds)
    ids = [i for i, k in conn.execute("SELECT id, kind FROM units WHERE name = ? ORDER BY id", (named,)) if not kinds or k in kinds]
    if len(ids) == 1:
        return ids[0]
    ranked = rank_files(cfg, conn, question, kinds)
    score = Counter()
    for s, _, unit in ranked[:k]:
        score[unit] += s
    return min(score, key=lambda u: (-score[u], u)) if score else None


def _symbol_in(question, conn):
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question):
        if conn.execute("SELECT 1 FROM facts WHERE kind = 'type' AND name = ? LIMIT 1", (tok,)).fetchone():
            return tok
    return None


def how(cfg, conn, question):
    r, score, data = route(cfg, question)
    base = {"version": VERSION, "question": question}
    if not r:
        return dict(base, route=None, score=score, command=None, tier=None, model=None, unresolved=[])
    edit = r["id"].startswith("packet")  # where the work is, not a unit name that happens to be a task word
    slots = {"unit": (edit and _unit_by_files(cfg, conn, question)) or _unit_in(question, conn),
             "symbol": _symbol_in(question, conn), "task": question.replace('"', "'")}
    unresolved = sorted(k for k in slots if "{%s}" % k in r["command"] and not slots[k])
    cmd = r["command"].format(**{k: v or "{%s}" % k for k, v in slots.items()})
    return dict(base, route=r["id"], score=score, command=f"{data['router']['bin']} {cmd}", tier=r["tier"],
                model=cfg.d["models"].get(r["tier"]), unresolved=unresolved)


def render(o):
    if not o["route"]:
        return f"no route scored above threshold (best score {o['score']}); logged as no-match"
    extra = f"   (unresolved: {', '.join(o['unresolved'])})" if o["unresolved"] else ""
    return f"{o['route']}  tier={o['tier']}  model={o['model'] or '-'}\n  {o['command']}{extra}"
