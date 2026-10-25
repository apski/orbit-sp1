"""Request log (R9): one JSON line per command in runs/requests.jsonl. Logs may carry clocks; derived data may not."""
import json
import time
from collections import Counter

from . import VERSION, router


def _path(cfg):
    p = cfg.own(cfg.d["project"]["runs"]) / "requests.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write(cfg, rec):
    with _path(cfg).open("a") as f:
        f.write(json.dumps(dict(rec, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), sort_keys=True) + "\n")


def read(cfg):
    p = _path(cfg)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _epoch(ts):
    return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def review(cfg, days=7, window_s=600):
    """Weekly review: misroutes, no-match questions, errors, slow commands -> proposed route fixes (never edits)."""
    recs = read(cfg)
    if recs:
        newest = max(_epoch(r["ts"]) for r in recs)
        recs = [r for r in recs if _epoch(r["ts"]) >= newest - days * 86400]
    recs.sort(key=lambda r: (r["ts"], r["command"]))
    data, _ = cfg.load_own("routes")
    by_cmd = {r["command"].split()[0]: r for r in reversed(data["routes"])}
    slow_ms = cfg.d["reqlog"]["slow_ms"]
    misroutes, proposals = [], []
    for i, r in enumerate(recs):
        if r["command"] != "how" or not r.get("suggested"):
            continue
        nxt = next((n for n in recs[i + 1:] if n["command"] != "how"), None)
        if nxt and _epoch(nxt["ts"]) - _epoch(r["ts"]) <= window_s and nxt["command"] != r["suggested"]:
            misroutes.append({"question": r["question"], "suggested": r["suggested"], "actual": nxt["command"]})
            target = by_cmd.get(nxt["command"])
            if target:
                new = sorted(set(router.tokens(r["question"])) - {router._norm(w) for w in target["words"]} - router.STOP)
                proposals.append({"route": target["id"], "add_words": new, "because": r["question"]})
    nomatch = sorted({r["question"] for r in recs if r["command"] == "how" and r["outcome"] == "no-match"})
    proposals += [{"route": None, "add_words": sorted(set(router.tokens(q)) - router.STOP), "because": q} for q in nomatch]
    return {
        "version": VERSION, "requests": len(recs), "days": days,
        "by_command": dict(sorted(Counter(r["command"] for r in recs).items())),
        "errors": [{"command": r["command"], "args": r.get("args"), "error": r.get("error")} for r in recs if r["outcome"] == "error"][-10:],
        "slow": sorted(({"command": r["command"], "args": r.get("args"), "duration_ms": r["duration_ms"]} for r in recs if r["duration_ms"] > slow_ms),
                       key=lambda s: (-s["duration_ms"], s["command"]))[:10],
        "no_match": nomatch, "misroutes": misroutes, "proposals": proposals,
    }


def render(o):
    out = [f"{o['requests']} requests in the last {o['days']} days: " + ", ".join(f"{k} x{v}" for k, v in o["by_command"].items())]
    for title, key in (("errors", "errors"), ("slow", "slow"), ("no-match", "no_match"), ("misroutes", "misroutes"), ("proposals (a person merges)", "proposals")):
        out += [f"\n{title}: {len(o[key])}"] + [f"  {x}" for x in o[key]]
    return "\n".join(out)
