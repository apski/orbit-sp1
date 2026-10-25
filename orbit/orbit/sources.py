"""L1 external source adapter (spec 4.3): read-only. A missing source is `unavailable`, never an empty success."""
from . import config


def read(cfg):
    out = {}
    for name, s in sorted(cfg.d.get("sources", {}).items()):
        paths = sorted(p for p in cfg.root.glob(s["glob"]) if p.is_file())
        if not paths:
            out[name] = {"status": "unavailable", "records": []}
            continue
        out[name] = {"status": "available", "records": [
            {"id": p.stem, "kind": s["kind"], "source_path": str(p.relative_to(cfg.root)), "hash": config.file_hash(p)} for p in paths]}
    return out
