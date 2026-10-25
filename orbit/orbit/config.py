"""orbit.toml loader. Config is data (R11); the config hash is stamped into derived output (R5)."""
import hashlib
import tomllib
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "examples" / "orbit.toml"  # repos pass their own via $ORBIT_CONFIG


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


class Config:
    def __init__(self, path=None):
        self.path = Path(path or DEFAULT).resolve()
        self.dir = self.path.parent
        self.d = tomllib.loads(self.path.read_text())
        self.hash = file_hash(self.path)
        p = self.d["project"]
        self.name = p["name"]
        self.root = (self.dir / p["root"]).resolve()

    def own(self, rel):
        """Path to a file Orbit owns (routes, runs, db): relative to orbit.toml."""
        return self.dir / rel

    def load_own(self, key):
        """Load a TOML data file named by [project].<key>; returns (data, hash)."""
        p = self.own(self.d["project"][key])
        return tomllib.loads(p.read_text()), file_hash(p)
