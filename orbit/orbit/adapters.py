"""L1 language adapter (regex). Config supplies the patterns; this is the only code that parses source.

ponytail: regex only, no tree-sitter. Refs are textual (comments filtered by prefix, strings are not).
Upgrade path: add a grammar adapter behind parse() and keep this as the fallback + parity oracle.

ui_literal (config `ui_literal` regex with groups api, text): one fact per user-visible string literal passed
straight to a text-bearing API. A call whose first argument is L(...) or a variable never matches. Known limits:
one line at a time (a literal on the line after `Text(` is missed, so is a triple-quoted string); a nested
quote inside an interpolation ends the match early; braces inside strings can end a #Preview skip early;
Text(verbatim:) is treated as a deliberate opt-out.
"""
import re

ADAPTER_VERSION = "regex-2"


def compile_langs(cfg):
    langs = {}
    for name, c in cfg.d["languages"].items():
        langs[name] = {
            "name": name,
            "ext": tuple(c["ext"]),
            "imports": [re.compile(p) for p in c.get("imports", [])],
            "type": re.compile(c["type"]) if "type" in c else None,
            "ref": re.compile(c["ref"]) if "ref" in c else None,
            "heading": re.compile(c["heading"]) if "heading" in c else None,
            "comment": tuple(c.get("comment", [])),
            "block": [tuple(b) for b in c.get("block", [])],
            "ui_literal": re.compile(c["ui_literal"]) if "ui_literal" in c else None,
            "ui_skip": tuple(c.get("ui_skip", [])),
        }
    return langs


def lang_for(path, langs):
    for lang in langs.values():
        if path.endswith(lang["ext"]):
            return lang
    return None


def _modules(raw):
    for part in raw.split(","):
        part = re.sub(r"\s+as\s+\w+$", "", part.strip())
        if part:
            yield part


def _bases(raw):
    """Split 'A, B(x), C[T], D<T>' into base names; drops kwargs like metaclass=M."""
    out, depth, cur = [], 0, ""
    for ch in raw + ",":
        if ch in "[(<":
            depth += 1
        elif ch in "])>":
            depth -= 1
        if ch == "," and depth == 0:
            name = re.match(r"\s*([A-Za-z_][\w.]*)\s*(?:[\[(<].*)?$", cur.strip())
            if name and "=" not in cur.split("[")[0].split("(")[0]:
                out.append(name.group(1))
            cur = ""
        else:
            cur += ch
    return out


def _has_words(lit):
    """True when a string literal shows letters once Swift `\\(...)` interpolations and escapes are removed.
    An interpolation is cut at its balanced `)`; an unclosed one (a nested quote ended the regex match) runs to the end."""
    out, i = [], 0
    while (j := lit.find("\\(", i)) >= 0:
        out.append(lit[i:j])
        depth, i = 1, j + 2
        while i < len(lit) and depth:
            depth += {"(": 1, ")": -1}.get(lit[i], 0)
            i += 1
    out.append(lit[i:])
    return re.search(r"[^\W\d_]", re.sub(r"\\u\{[^}]*\}|\\.", "", "".join(out))) is not None


def parse(lang, text):
    """-> [(kind, name, line, data)] facts for one file, in file order."""
    facts, fenced, block_end, skip = [], False, None, None
    for n, line in enumerate(text.splitlines(), 1):
        if lang["block"]:  # docstrings: skip bodies so prose is not read as code
            s = line.strip()
            if block_end:
                block_end = None if block_end in line else block_end
                continue
            opened = next(((a, b) for a, b in lang["block"] if s.startswith(a)), None)
            if opened:
                block_end = None if opened[1] in s[len(opened[0]):] else opened[1]
                continue
        if lang["heading"]:  # markdown: headings outside code fences
            fenced ^= line.lstrip().startswith("```")
            m = None if fenced else lang["heading"].match(line)
            if m:
                facts.append(("heading", m.group("name"), n, {"level": len(m.group("level"))}))
            continue
        imported = False
        for rx in lang["imports"]:
            m = rx.match(line)
            if m:
                imported = True
                for mod in _modules(m.group(1)):
                    facts.append(("import", mod, n, {}))
                break
        if lang["ui_literal"]:  # user-visible literals; ui_skip blocks (#Preview) are brace-balanced and not shipped
            s = line.lstrip()
            if skip is None and lang["ui_skip"] and s.startswith(lang["ui_skip"]):
                skip = [0, False]  # [brace depth, opened]
            if skip is not None:
                skip[0] += line.count("{") - line.count("}")
                skip[1] = skip[1] or "{" in line
                if skip[1] and skip[0] <= 0:
                    skip = None
            elif not s.startswith(lang["comment"]):
                for lm in lang["ui_literal"].finditer(line):
                    if _has_words(lm.group("text")):
                        facts.append(("ui_literal", lm.group("text")[:60], n, {"api": lm.group("api")}))
        m = lang["type"].match(line) if lang["type"] else None
        if m:
            g = m.groupdict()
            bases = _bases(", ".join(filter(None, [g.get("bases"), g.get("impls")])))
            facts.append(("type", g["name"], n, {"kind": g["kind"], "visibility": (g.get("vis") or "").strip() or None, "bases": bases}))
            for b in bases:
                facts.append(("protocol", b, n, {"type": g["name"]}))
        if imported or line.lstrip().startswith(lang["comment"]):
            continue
        for sym in sorted(set(lang["ref"].findall(line))) if lang["ref"] else ():
            facts.append(("ref", sym, n, {}))
    return facts
