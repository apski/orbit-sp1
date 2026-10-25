"""Check adapter (spec 4.4, 8.4). The only module (with the CLI's own process) that starts external processes (R7).

Status: passed | failed | error | unavailable (tool missing, never "passed") | planned (no command yet).
Exit non-zero if any required check failed or errored.
"""
import os
import shutil
import subprocess
import time

from . import VERSION
import json


def _tool_missing(cfg, exe):
    return not (cfg.root / exe).exists() if "/" in exe else shutil.which(exe) is None


def run_check(cfg, cid, c, runs, stamp):
    res = {"version": VERSION, "check": cid, "status": "planned", "required": bool(c.get("required")),
           "command": c.get("command"), "exit_code": None, "duration_ms": 0, "log": None, "evidence": []}
    if "command" in c:
        log = runs / f"{stamp}-{cid}.log"
        t0 = time.monotonic()
        if _tool_missing(cfg, c["command"][0]):
            res["status"] = "unavailable"
        else:
            try:
                p = subprocess.run(c["command"], cwd=cfg.root, capture_output=True, text=True, timeout=c.get("timeout", 300))
                log.write_text(p.stdout + p.stderr)
                res.update(status="passed" if p.returncode == 0 else "failed", exit_code=p.returncode, log=str(log.relative_to(cfg.dir)))
                if p.returncode:
                    res["evidence"] = (p.stdout + p.stderr).strip().splitlines()[-15:]
            except subprocess.TimeoutExpired:
                log.write_text("timeout")
                res.update(status="error", log=str(log.relative_to(cfg.dir)), evidence=[f"timeout after {c.get('timeout', 300)}s"])
        res["duration_ms"] = round((time.monotonic() - t0) * 1000)
    (runs / f"{stamp}-{cid}.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    return res


def verify(cfg, target):
    sets, checks = cfg.d["check_sets"], cfg.d["checks"]
    ids = sets[target] if target in sets else [target]
    unknown = [i for i in ids if i not in checks]
    if unknown:
        raise ValueError(f"unknown check/check-set {unknown[0]!r}; sets: {', '.join(sorted(sets))}; checks: {', '.join(sorted(checks))}")
    runs = cfg.own(cfg.d["project"]["runs"])
    runs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    results = [run_check(cfg, i, checks[i], runs, stamp) for i in ids]
    bad = [r for r in results if r["required"] and r["status"] in ("failed", "error")]
    return {"version": VERSION, "check_set": target, "results": results, "exit": 1 if bad else 0}


def render(o):
    out = []
    for r in o["results"]:
        mark = {"passed": "ok", "failed": "FAIL", "error": "FAIL"}.get(r["status"], f"SKIP({r['status']})")
        out.append(f"STEP {r['check']} {mark} {r['duration_ms'] / 1000:.1f}s")
        if r["status"] in ("failed", "error"):
            out += [f"    {l}" for l in (r["evidence"] if not os.environ.get("VERBOSE") else open(r["log"]).read().splitlines())]
    out.append(f"verify {o['check_set']}: {'FAIL' if o['exit'] else 'ok'}")
    return "\n".join(out)
