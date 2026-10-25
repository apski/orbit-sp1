"""Golden tests for the bootstrap census (SPEC R12). Run: python3 -m unittest discover -s agents/orbit/bootstrap/tests

The fixture (Swift app + RN app + Python API + Cargo workspace, Orbit covering only the API and the Next corner)
is copied to a temp dir and committed twice with pinned dates and identity, so HEAD and churn are the same on every
machine. PATH holds only git and a fake `rg`, so the tool checks are the same on every machine too.
A changed output must update the golden and say why in the commit:
UPDATE_GOLDEN=1 python3 -m unittest discover -s agents/orbit/bootstrap/tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE.parent / "census.py"
GOLDEN = HERE / "golden"
# Isolate git from the user's config and global excludes (XDG), which would change what ls-files returns.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "XDG_CONFIG_HOME": os.devnull, "GIT_AUTHOR_NAME": "fixture",
           "GIT_AUTHOR_EMAIL": "fixture@example.com", "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com"}


class Census(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        repo = cls.repo = cls.tmp / "repo"
        shutil.copytree(HERE / "fixture", repo)
        shutil.copytree(HERE / "skills-home", cls.tmp / "skills-home")
        bin_ = cls.tmp / "bin"
        bin_.mkdir()
        (bin_ / "git").symlink_to(os.path.realpath(shutil.which("git")))
        (bin_ / "rg").write_text("#!/bin/sh\n")
        (bin_ / "rg").chmod(0o755)
        cls.env = dict(os.environ, **GIT_ENV, PATH=str(bin_))
        # The fixture is tiny, so let every language through the "add a language block" threshold.
        cat = (HERE.parent / "catalog.toml").read_text().replace("min_lines = 200", "min_lines = 1")
        (cls.tmp / "catalog.toml").write_text(cat)

        def git(*args, date):
            env = dict(cls.env, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

        # Commit 1 is outside the 90-day window of commit 2, so mobile/ (touched only by commit 1) is frozen.
        git("init", "-q", "-b", "main", date="2025-06-01T00:00:00Z")
        git("add", "-A", date="2025-06-01T00:00:00Z")
        git("commit", "-q", "-m", "fixture", date="2025-06-01T00:00:00Z")
        for rel in ("ios/App/Views/Home/HomeView.swift", "api/services/billing/pricing.py", "rust/core/src/lib.rs", "web/src/main.ts"):
            with open(repo / rel, "a") as f:
                f.write("// touched\n")
        git("commit", "-q", "-am", "touch home", date="2026-02-01T00:00:00Z")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_census(self, *args):
        p = subprocess.run([sys.executable, str(CENSUS), *args, "--repo", str(self.repo), "--catalog", str(self.tmp / "catalog.toml"),
                            "--skill-root", ".agents/skills", "--skill-root", "../skills-home"],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def golden(self, name, out):
        path = GOLDEN / name
        if os.environ.get("UPDATE_GOLDEN"):
            GOLDEN.mkdir(exist_ok=True)
            path.write_text(out)
        self.assertEqual(out, path.read_text(), f"{name} drifted; UPDATE_GOLDEN=1 and say why in the commit")

    def test_golden_and_deterministic(self):
        policy = ["--policy", str(self.repo / "retire-mobile.toml")]
        for name, args in [("census.json", ["census", "--json"]), ("propose.json", ["propose", "--json"]), ("report.md", ["report"]),
                           ("propose-policy.json", ["propose", "--json", *policy])]:
            with self.subTest(name):
                first = self.run_census(*args)
                self.assertEqual(first, self.run_census(*args), "two runs differ")
                self.golden(name, first)

    def test_no_machine_paths(self):
        out = self.run_census("propose", "--json") + self.run_census("census", "--json")
        self.assertNotIn(str(self.tmp), out)
        self.assertNotIn(str(Path.home()), out)

    def test_findings(self):
        cen = json.loads(self.run_census("census", "--json"))
        p = json.loads(self.run_census("propose", "--json"))
        sig = {s["id"] for s in cen["signals"]}
        self.assertTrue({"xcodeproj", "swiftui", "uikit", "xctest", "react-native", "expo", "jest", "python-web", "pytest",
                         "github-actions", "claude-md"} <= sig, sig)
        ios = next(t for t in cen["targets"] if t["root"] == "ios")
        self.assertEqual((ios["domain"], ios["schemes"], ios["commits"]), ("mobile", ["SampleApp"], 1))
        items = {(i["kind"], i["id"]): i for i in p["items"]}
        # Swift outside Next is unindexed and named as such.
        inc = items[("include", "ios/**")]
        self.assertEqual(inc["gap_files"], 5)  # Home, Settings, Legacy, AppTests, AppUITests
        self.assertIn("ios/App/Views/Next/**", inc["why"])
        self.assertIn("ios/App/Views/Next", items[("unit", "ios/App/Views/*")]["change"])  # exclude the existing kind's parent
        self.assertIn(("language", "typescript"), items)
        self.assertIn(("check", "xcodebuild-test:ios"), items)
        self.assertNotIn(("check", "py-compile:api"), items)  # Orbit already runs it
        self.assertIn(("skill", "axiom-build"), items)  # MISSING from every root
        self.assertNotIn(("skill", "rn-perf-climb"), items)  # found in ../skills-home
        # Short kit symbols the ref regex cannot see, unlinked specs, stale skill paths, Cargo crates, tools.
        self.assertIn("SKD (2 uses)", items[("refs", "swift")]["why"])
        spec = items[("spec", "docs/specs")]
        self.assertIn('units = ["Feed"]', spec["change"])
        self.assertIn("onboarding.spec.md", spec["why"])
        self.assertEqual(items[("skill-ref", "local-helper")]["evidence"], ["GoneService.swift", "ios/App/Missing.swift"])
        self.assertIn('glob = "rust/*"', items[("unit", "rust/*")]["change"])
        self.assertIn("--manifest-path rust/Cargo.toml -p core", items[("check", "cargo-test:rust/core")]["change"])
        self.assertIn(("language", "rust"), items)
        self.assertIn(("tool", "pdf-to-text"), items)  # always needed, not on the test PATH
        self.assertIn("local-helper", items[("tool", "jq")]["why"])  # a skill calls it
        self.assertNotIn(("tool", "ripgrep"), items)  # fake rg is on PATH
        self.assertIn("2026-02-01", [t["last_commit"] for t in cen["targets"] if t["root"] == "ios"])
        # score = (commits + 1) x gap / scope, sorted descending
        scores = [i["score"] for i in p["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(i["score"] == round((i["commits"] + 1) * i["gap_files"] / max(i["scope_files"], 1), 2) for i in p["items"]))

    def test_policy_retire(self):
        p = json.loads(self.run_census("propose", "--json", "--policy", str(self.repo / "retire-mobile.toml")))
        items = {(i["kind"], i["id"]): i for i in p["items"]}
        self.assertIn(("retire", "mobile"), items)
        self.assertIn("RN app retired", items[("retire", "mobile")]["why"])
        # No integrate work is proposed inside the retired path.
        self.assertFalse([k for k in items if k[0] != "retire" and k[1].startswith(("mobile", "ts-rn", "jest:mobile", "tsc:mobile", "rn-"))])
        self.assertNotIn(("skill", "argent-react-native-app-workflow"), items)
        self.assertNotIn("rn-ui-change", [r["task"] for r in p["routing"]])
        self.assertEqual(p["policy"]["retire"], ["mobile"])
        cen = json.loads(self.run_census("census", "--json", "--policy", str(self.repo / "retire-mobile.toml")))
        self.assertEqual(cen["demand"], [{"bucket": "ios-home", "commits": 1, "regex": "^ios/App/Views/Home/"}])

    def test_mobile_contract(self):
        """ORBIT-MOBILE-DESIGN.md section 2: one active Xcode target, one frozen RN target."""
        cen = json.loads(self.run_census("census", "--json"))
        p = json.loads(self.run_census("propose", "--json"))
        t = {x["root"]: x for x in cen["targets"]}
        ios, mobile = t["ios"], t["mobile"]
        self.assertEqual(ios["test_bundles"], ["ios/AppTests", "ios/AppUITests"])
        self.assertEqual(ios["flows"], [{"dir": "ios/.argent/flows", "count": 1}])
        self.assertIn({"path": "ios/App/Views", "code_files": 4, "lines": 29, "commits": 1}, ios["tree"])
        self.assertEqual([r["path"] for r in ios["tree"]], sorted(r["path"] for r in ios["tree"]))
        self.assertEqual((mobile["commits"], mobile["last_commit"], mobile["workspace_deps"]), (0, "2025-06-01", ["shared"]))
        self.assertEqual(t["web"]["workspace_deps"], ["shared"])
        # The Xcode target gets unit and check items; the frozen RN target exactly one retire item and nothing else.
        ids = {(i["kind"], i["id"]) for i in p["items"]}
        self.assertIn(("unit", "ios/App/Views/*"), ids)
        self.assertIn(("check", "xcodebuild-test:ios"), ids)
        rn = [i for i in p["items"] if i["id"].startswith(("mobile", "ts-rn")) or i["id"].endswith(":mobile") or "mobile:" in " ".join(i["evidence"])]
        self.assertEqual([(i["kind"], i["id"]) for i in rn], [("retire", "mobile")])
        inv = rn[0]["inventory"]
        self.assertEqual(inv["workspace_deps"], [{"root": "shared", "also_used_by": ["web"], "action": "keep, prune"}])
        self.assertEqual(inv["last_commit"], "2025-06-01")
        # The RN app reuses the live iOS app's bundle id: retiring it must not retire the App Store record.
        self.assertEqual(inv["external_ids"], [{"id": "com.fx.app", "also_used_by": ["ios"], "action": "keep (live target uses it)"},
                                               {"id": "com.fx.mobile", "also_used_by": [], "action": "retire"}])
        self.assertIn("frozen", rn[0]["why"])
        # Routing task ids are the Orbit recipe ids.
        tasks = {r["task"] for r in p["routing"]}
        self.assertTrue({"ios-ui-change", "ios-networking", "ios-purchase", "ios-perf", "ios-release", "ios-screen-port", "ios-localization"} <= tasks, tasks)
        self.assertFalse({"rn-ui-change", "rn-perf", "rn-debug"} & tasks)


if __name__ == "__main__":
    unittest.main()
