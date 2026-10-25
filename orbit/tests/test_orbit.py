"""Orbit acceptance tests (spec sections 10-12). Run: python3 -m unittest discover -s agents/orbit/tests -t agents/orbit

Golden files live in tests/golden/. A changed output must update the golden and say why in the commit:
UPDATE_GOLDEN=1 python3 -m unittest discover -s agents/orbit/tests -t agents/orbit
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORBIT = HERE.parent
sys.path.insert(0, str(ORBIT))
FIXTURE = HERE / "fixture"
GOLDEN = HERE / "golden"
ENV = dict(os.environ, PYTHONPATH=str(ORBIT))


def orbit(*args, config=FIXTURE / "orbit.toml", cwd=None):
    p = subprocess.run([sys.executable, "-m", "orbit", "--config", str(config), *args], cwd=cwd or ORBIT, env=ENV, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def jorbit(*args, **kw):
    code, out, err = orbit(*args, "--json", **kw)
    return code, json.loads(out), err


def scrub(o):
    if isinstance(o, dict) and "git" in o:  # base commit and dirty files describe the outer repo, not the fixture
        o = {k: v for k, v in o.items() if k != "git"}
    return o


GOLDEN_CASES = {
    "index": ["index"],
    "status": ["status"],
    "observe-billing": ["observe", "billing"],
    "observe-checkout": ["observe", "checkout"],
    "observe-feed": ["observe", "Feed"],
    "observe-tally": ["observe", "tally"],
    "refs-matcher": ["refs", "Matcher"],
    "refs-pricer": ["refs", "Pricer"],
    "artifacts-billing": ["artifacts", "billing"],
    "artifacts-ledger": ["artifacts", "ledger"],
    "principles": ["principles", "check"],
    "diagram-billing": ["diagram", "billing"],
    "packet-billing": ["packet", "billing", "fix the pricing rules"],
    "how-observe": ["how", "what does billing contain, which layers"],
    "how-nomatch": ["how", "banana split"],
    "perf": ["perf", "tests/fixture/perf"],
}


class Golden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(FIXTURE / ".cache", ignore_errors=True)
        orbit("index")

    def test_golden(self):
        for name, args in GOLDEN_CASES.items():
            with self.subTest(name):
                _, got, _ = jorbit(*args)
                got = scrub(got)
                path = GOLDEN / f"{name}.json"
                if os.environ.get("UPDATE_GOLDEN"):
                    path.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
                self.assertTrue(path.exists(), f"no golden for {name}: run with UPDATE_GOLDEN=1 and explain why in the commit")
                self.assertEqual(got, json.loads(path.read_text()), f"{name} drifted from golden")

    def test_stdout_is_byte_stable(self):
        a, b = orbit("observe", "billing", "--json")[1], orbit("observe", "billing", "--json")[1]
        self.assertEqual(a, b)
        self.assertEqual(a, json.dumps(json.loads(a), indent=2, sort_keys=True) + "\n")


class Index(unittest.TestCase):
    def test_second_run_parses_zero_files_and_output_is_identical(self):  # bootstrap step 3
        orbit("index", "--rebuild")
        c1, o1, e1 = orbit("index")
        c2, o2, e2 = orbit("index")
        self.assertEqual(o1, o2)
        self.assertIn("parsed=0", e2)

    def test_edit_reparses_only_that_file(self):
        f = FIXTURE / "py/ledger/core/ledger.py"
        orig = f.read_text()
        try:
            orbit("index")
            time.sleep(0.01)
            f.write_text(orig + "\nclass Extra:\n    pass\n")
            _, out, err = orbit("index")
            self.assertIn("parsed=1", err)
            self.assertIn("Extra", orbit("refs", "Extra")[1])
        finally:
            f.write_text(orig)
            orbit("index")

    def test_config_change_forces_full_rebuild(self):  # R5: never a silent migration
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "fx"
            shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".cache", ".runs"))
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            cfg = d / "orbit.toml"
            orbit("index", config=cfg)
            cfg.write_text(cfg.read_text() + "\n# touched\n")
            self.assertRegex(orbit("index", config=cfg)[2], r"parsed=1[0-9]")

    def test_parse_ok_rate(self):  # bootstrap step 2: >= 95% of code files parse ok
        _, st, _ = jorbit("status")
        code = [l for l in st["languages"] if l["name"] != "-"]
        self.assertGreaterEqual(sum(l["ok"] for l in code) / sum(l["files"] for l in code), 0.95)

    def test_identical_across_two_machines(self):  # acceptance 1: copy to a fresh clone-like dir, same bytes
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "fx"
            shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".cache", ".runs"))
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            for args in (["index"], ["observe", "billing"], ["packet", "billing", "fix the pricing rules"]):
                a = scrub(jorbit(*args)[1])
                b = scrub(jorbit(*args, config=d / "orbit.toml", cwd=d)[1])
                self.assertEqual(a, b, args)


class Citations(unittest.TestCase):
    def test_every_answer_cites_a_source(self):  # acceptance 2
        for args in (["observe", "billing"], ["observe", "Feed"]):
            _, o, _ = jorbit(*args)
            for key in ("imports", "types", "protocols"):
                for e in o[key]:
                    self.assertRegex(e["source"], r"^.+:\d+$", (args, key, e))
        _, o, _ = jorbit("refs", "Pricer")
        self.assertTrue(all(re.match(r"^.+:\d+$", r["source"]) for r in o["definitions"] + o["references"]))
        _, o, _ = jorbit("principles", "check")
        self.assertTrue(all(v["file"] and v["line"] for r in o["principles"] for v in r["violations"]))


class Packet(unittest.TestCase):
    def test_every_bullet_has_a_citation_and_mandatory_sections_lead(self):
        _, o, _ = jorbit("packet", "billing", "fix the pricing rules")
        bullets = [l for l in o["markdown"].splitlines() if l.startswith("- ")]
        self.assertTrue(bullets)
        for l in bullets:
            self.assertRegex(l, r"  \[[^\]\s]+\]$", l)
        titles = [s["title"] for s in o["sections"]]
        self.assertEqual(titles[:3], ["Contract", "Invariants", "Verify"])
        self.assertTrue(all(s["mandatory"] for s in o["sections"][:3]))

    def test_contract_comes_from_headings_outside_code_fences(self):
        _, o, _ = jorbit("packet", "billing", "pricing")
        self.assertIn("Pricing rules", o["markdown"])
        self.assertNotIn("not a heading", o["markdown"])

    def test_missing_contract_is_reported_not_invented(self):
        _, o, _ = jorbit("packet", "ledger", "change the ledger")
        self.assertIn("MISSING", o["markdown"])

    def test_budget_never_exceeded_without_over_budget_flag(self):  # spec 10
        for budget in (50, 120, 200, 400, 1200):
            _, o, _ = jorbit("packet", "billing", "fix the pricing rules", "--budget", str(budget))
            self.assertEqual(o["over_budget"], o["tokens"] > budget)
            if not o["over_budget"]:
                self.assertLessEqual(o["tokens"], budget)
        _, tiny, _ = jorbit("packet", "billing", "fix the pricing rules", "--budget", "50")
        self.assertTrue(tiny["over_budget"])  # mandatory sections alone exceed 50 tokens
        self.assertTrue(tiny["dropped"])  # and what was left out is recorded

    def test_tier_comes_from_the_router_unless_given(self):
        self.assertEqual(jorbit("packet", "billing", "fix the bug")[1]["tier"], "build")
        self.assertEqual(jorbit("packet", "billing", "fix the bug", "--tier", "explain")[1]["budget"], 1200)


class Artifacts(unittest.TestCase):
    def test_present_and_missing_never_false_present(self):  # bootstrap step 7
        _, o, _ = jorbit("artifacts", "billing")
        st = {a["name"]: a["status"] for a in o["artifacts"]}
        self.assertEqual(st, {"facts": "derived", "tests": "present", "doc": "present"})
        _, o, _ = jorbit("artifacts", "ledger")
        self.assertEqual({a["name"]: a["status"] for a in o["artifacts"]}, {"facts": "derived", "tests": "missing", "doc": "missing"})

    def test_stale_when_source_is_newer(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "fx"
            shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".cache", ".runs"))
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            cfg = d / "orbit.toml"
            cfg.write_text(cfg.read_text().replace("stale_if_source_newer = false", "stale_if_source_newer = true"))
            old = 946684800  # 2000-01-01: copytree keeps the fixture's real mtimes, so "now - N" goes stale as the fixture ages
            for p in (d / "tests/test_billing_pricing.py", d / "docs/billing-notes.md", d / "docs/billing-spec.md"):
                os.utime(p, (old, old))
            for p in d.rglob("*"):  # copytree keeps the checkout mtimes; sources must be newer than the aged artifacts
                if p.is_file() and p.stat().st_mtime != old:
                    os.utime(p)
            _, o, _ = jorbit("artifacts", "billing", config=cfg, cwd=d)
            self.assertEqual({a["name"]: a["status"] for a in o["artifacts"]}["tests"], "stale")


class Principles(unittest.TestCase):
    def test_every_predicate_fires_with_file_line(self):  # bootstrap step 8
        code, o, _ = jorbit("principles", "check")
        got = {r["id"]: len(r["violations"]) for r in o["principles"]}
        self.assertEqual(got, {"p-layers": 2, "p-localized": 11, "p-no-requests": 1, "p-no-userdefaults": 1, "p-one-rule": 1, "p-small": 2})
        self.assertEqual(code, 1)  # the enforced one fails the check
        self.assertEqual(o["summary"], {"principles": 6, "observed_violations": 17, "enforced_violations": 1})

    def test_ui_literal_skips_localized_preview_and_non_text(self):  # fixture swift/Feed/View/FeedStrings.swift
        _, o, _ = jorbit("principles", "check")
        got = [(v["line"], v["detail"]) for r in o["principles"] if r["id"] == "p-localized" for v in r["violations"] if v["file"].endswith("FeedStrings.swift")]
        self.assertEqual([ln for ln, _ in got], [9, 15, 16, 17, 18, 21, 21, 23, 24, 25])
        self.assertNotIn("shows gearshape", [d for _, d in got])

    def test_observed_never_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "fx"
            shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".cache", ".runs"))
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            p = d / "principles.toml"
            p.write_text(p.read_text().replace('"enforced"', '"observed"'))
            code, o, _ = jorbit("principles", "check", config=d / "orbit.toml", cwd=d)
            self.assertEqual((code, o["summary"]["enforced_violations"]), (0, 0))

    def test_unknown_predicate_is_rejected(self):  # closed set
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "fx"
            shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".cache", ".runs"))
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            (d / "principles.toml").write_text(
                '[[principle]]\nid="x"\nstatement="s"\nwhy="w"\nsource="s"\nstatus="observed"\nscope={}\npredicate={kind="made_up"}\n')
            code, _, err = orbit("principles", "check", config=d / "orbit.toml", cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("unknown predicate", err)


class Verify(unittest.TestCase):
    def test_statuses_exit_code_and_result_files(self):  # bootstrap step 10
        shutil.rmtree(FIXTURE / ".runs", ignore_errors=True)
        code, o, _ = jorbit("verify", "all")
        st = {r["check"]: r["status"] for r in o["results"]}
        self.assertEqual(st, {"ok-check": "passed", "bad-check": "failed", "optional-bad": "failed", "no-tool": "unavailable", "later": "planned"})
        self.assertEqual(code, 1)  # a required check failed
        for r in o["results"]:
            if r["status"] in ("passed", "failed"):
                self.assertTrue((FIXTURE / r["log"]).exists())
        results = list((FIXTURE / ".runs").glob("*-bad-check.json"))
        self.assertEqual(len(results), 1)
        self.assertEqual(json.loads(results[0].read_text())["exit_code"], 3)

    def test_unavailable_is_never_passed_and_optional_failure_does_not_block(self):
        code, o, _ = jorbit("verify", "good")  # ok + planned + unavailable-required: spec exits non-zero only on failed/error
        self.assertEqual({r["check"]: r["status"] for r in o["results"]}, {"ok-check": "passed", "later": "planned", "no-tool": "unavailable"})
        self.assertEqual(code, 0)
        self.assertEqual(jorbit("verify", "optional-bad")[0], 0)

    def test_unknown_check_is_an_error(self):
        self.assertEqual(orbit("verify", "nope")[0], 2)


class Router(unittest.TestCase):
    def test_accuracy_is_measured_and_must_not_regress(self):  # acceptance 6
        sys.path.insert(0, str(ORBIT))
        from orbit import router
        from orbit.config import Config
        cfg = Config()
        qs = tomllib.loads((HERE / "questions.toml").read_text())
        miss = []
        for q in qs["q"]:
            r, score, _ = router.route(cfg, q["text"])
            if (r["id"] if r else "none") != q["expect"]:
                miss.append((q["text"], r["id"] if r else "none", q["expect"]))
        acc = 1 - len(miss) / len(qs["q"])
        print(f"\nrouting accuracy: {acc:.3f} on {len(qs['q'])} labelled questions (floor {qs['floor']}); misses: {miss}", file=sys.stderr)
        self.assertGreaterEqual(acc, qs["floor"], miss)

    def test_slots_fill_from_the_index(self):
        _, o, _ = jorbit("how", "what does billing contain, which layers")
        self.assertEqual((o["route"], o["command"], o["unresolved"]), ("observe", "orbit observe billing", []))
        _, o, _ = jorbit("how", "what does it contain, which layers")
        self.assertEqual(o["unresolved"], ["unit"])  # never invent a unit

    def test_commit_style_task_routes_to_the_unit_whose_code_matches(self):
        _, o, _ = jorbit("how", "fix(swift): the reducer drops a feed row")  # header alone clears the threshold
        self.assertEqual((o["route"], o["command"].split()[:3]), ("packet", ["orbit", "packet", "swift-feat:Feed"]))
        _, o, _ = jorbit("how", "fix(py): taxer rounds the wrong way")  # "py" narrows to py-* units; code words pick billing
        self.assertEqual(o["command"].split()[2], "py-svc:billing")
        _, o, _ = jorbit("how", "fix the pricing bug in ledger")  # a unit named in its own case beats the word match
        self.assertEqual(o["command"].split()[2], "py-svc:ledger")

    def test_no_match_is_logged(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d) / "fx"
            shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".cache", ".runs"))
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            orbit("how", "banana split", config=d / "orbit.toml", cwd=d)
            rec = json.loads((d / ".runs/requests.jsonl").read_text().splitlines()[-1])
            self.assertEqual((rec["command"], rec["outcome"]), ("how", "no-match"))


class Review(unittest.TestCase):
    def test_review_reports_a_weeks_log(self):  # bootstrap step 6
        sys.path.insert(0, str(ORBIT))
        from orbit import reqlog
        from orbit.config import Config
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(FIXTURE / "orbit.toml")
            cfg.d["project"]["runs"] = d  # absolute path replaces the config dir in Path joins
            L = [
                ("2026-10-01T09:00:00Z", "how", dict(question="what does billing contain", suggested="observe", outcome="ok", duration_ms=40)),
                ("2026-10-01T09:00:30Z", "refs", dict(outcome="ok", duration_ms=20)),  # user ran refs, not observe: misroute
                ("2026-10-02T10:00:00Z", "how", dict(question="banana split", suggested=None, outcome="no-match", duration_ms=30)),
                ("2026-10-03T10:00:00Z", "packet", dict(outcome="ok", duration_ms=5000, args=["packet", "billing", "x"])),
                ("2026-10-04T10:00:00Z", "observe", dict(outcome="error", error="unknown unit 'zzz'", duration_ms=10, args=["observe", "zzz"])),
                ("2026-08-01T10:00:00Z", "index", dict(outcome="ok", duration_ms=10)),  # outside the window
            ]
            with (Path(d) / "requests.jsonl").open("w") as f:
                for ts, cmd, extra in L:
                    f.write(json.dumps(dict(ts=ts, command=cmd, args=extra.pop("args", []), **extra)) + "\n")
            r = reqlog.review(cfg, days=7)
        self.assertEqual(r["requests"], 5)
        self.assertEqual(r["by_command"], {"how": 2, "observe": 1, "packet": 1, "refs": 1})
        self.assertEqual(r["no_match"], ["banana split"])
        self.assertEqual(r["misroutes"], [{"question": "what does billing contain", "suggested": "observe", "actual": "refs"}])
        self.assertEqual([s["command"] for s in r["slow"]], ["packet"])
        self.assertEqual(r["errors"][0]["error"], "unknown unit 'zzz'")
        self.assertTrue(r["proposals"])  # proposals only; nothing edits routes.toml


class Schemas(unittest.TestCase):
    def _schema(self, name):
        return json.loads((ORBIT / "schemas" / f"{name}.schema.json").read_text())

    def _conforms(self, name, obj):
        s = self._schema(name)
        self.assertEqual(sorted(set(s["required"]) - set(obj)), [], name)
        if "version" in s["properties"]:
            self.assertEqual(obj["version"], s["properties"]["version"]["const"])
        for k, sub in s["properties"].items():
            if k in obj and "enum" in sub:
                self.assertIn(obj[k], sub["enum"])

    def test_outputs_conform(self):  # spec 9: every machine-readable output has a schema and a version
        self._conforms("observation", jorbit("observe", "billing")[1])
        self._conforms("packet", jorbit("packet", "billing", "fix it")[1])
        for r in jorbit("verify", "all")[1]["results"]:
            self._conforms("check-result", r)
        self._conforms("perf-report", jorbit("perf", "tests/fixture/perf")[1])

    def test_trace_fixture_events_use_known_kinds(self):
        kinds = set(self._schema("trace-event")["properties"]["k"]["enum"])
        for f in (FIXTURE / "perf").rglob("*.jsonl"):
            for line in f.read_text().splitlines():
                self.assertIn(json.loads(line)["k"], kinds, f)

    def test_principle_definitions_conform_and_predicate_enum_is_the_closed_set(self):
        from orbit import principles
        for p in tomllib.loads((FIXTURE / "principles.toml").read_text())["principle"] + tomllib.loads((ORBIT / "examples" / "principles.toml").read_text()).get("principle", []):
            self._conforms("principle", p)
        self.assertEqual(set(self._schema("principle")["properties"]["predicate"]["properties"]["kind"]["enum"]), set(principles.PREDICATES))


class Sources(unittest.TestCase):
    def test_missing_source_is_unavailable_never_empty(self):  # acceptance 4
        _, o, _ = jorbit("status")
        self.assertEqual(o["sources"], {"figma": "unavailable", "notes": "available"})


class Errors(unittest.TestCase):
    def test_unknown_and_ambiguous_units(self):
        code, _, err = orbit("observe", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown unit", err)
        self.assertEqual(orbit("observe", "py-svc:billing")[0], 0)


if __name__ == "__main__":
    unittest.main()


def trace(*events, name="t"):
    from orbit import perf
    return perf.parse("\n".join(json.dumps(e) for e in events), name)


def b(i, t, ph=None, th="main", p=None, n=None):
    return {k: v for k, v in {"k": "b", "id": i, "t": t, "ph": ph, "th": th, "p": p, "n": n or f"s{i}"}.items() if v is not None}


class Perf(unittest.TestCase):  # docs/orbit/perf/PERFSTACK.md section 5
    def test_buckets_sum_to_the_window_in_every_report_window(self):
        _, o, _ = jorbit("perf", "tests/fixture/perf")
        for w in o["windows"]:
            for arm, a in w["arms"].items():
                m = a["median_rep"]
                self.assertAlmostEqual(sum(m["buckets"].values()), m["window_ms"], delta=0.05 * len(m["buckets"]), msg=(w["window"], arm))

    def test_trade_screen_reproduces_the_designed_split(self):  # fixture built as 130/170/310/230 vs 40/100/110/100
        _, o, _ = jorbit("perf", "tests/fixture/perf")
        t = next(w for w in o["windows"] if w["window"] == "trade")
        self.assertEqual((t["arms"]["legacy"]["milestones"]["interactive"]["p50"], t["arms"]["next"]["milestones"]["interactive"]["p50"]), (840.0, 350.0))
        self.assertEqual(t["speedup"]["next"]["interactive"]["ratio"], 2.4)
        self.assertEqual(t["arms"]["legacy"]["median_rep"]["buckets"]["navigation"], 130.0)

    def test_overlap_is_reported_never_added(self):
        from orbit import perf
        tr = trace(b(1, 0, "render"), {"k": "e", "id": 1, "t": 100}, b(2, 0, "data", th="bg:25"), {"k": "e", "id": 2, "t": 100})
        at = perf.attribute(tr, 0, 100)
        self.assertEqual((at["buckets"], at["overlapped"]), ({"render": 100}, {"data": 100}))

    def test_priority_main_span_then_main_busy_then_background_then_waiting(self):
        from orbit import perf
        tr = trace(b(1, 0, "render"), {"k": "e", "id": 1, "t": 10},          # 0-10 main span
                   {"k": "busy", "th": "main", "t": 10, "d": 10},             # 10-20 main busy, no span (bg runs too)
                   b(2, 10, "data", th="bg:25"), {"k": "e", "id": 2, "t": 30})  # 20-30 main idle, bg data; 30-40 nothing
        self.assertEqual(perf.attribute(tr, 0, 40)["buckets"], {"render": 10, perf.UNINSTRUMENTED: 10, "data": 10, perf.WAITING: 10})

    def test_deepest_main_span_wins_and_phase_inherits(self):
        from orbit import perf
        tr = trace(b(1, 0, "data"), b(2, 2, None, p=1), {"k": "e", "id": 2, "t": 8}, {"k": "e", "id": 1, "t": 10})
        at = perf.attribute(tr, 0, 10)
        self.assertEqual((at["buckets"], at["self"]), ({"data": 10}, {1: 4, 2: 6}))

    def test_js_clock_uses_the_tightest_sync_sample(self):
        tr = trace({"k": "sync", "src": "js", "t0": 100, "t1": 140, "tn": 5000},   # rtt 40
                   {"k": "sync", "src": "js", "t0": 200, "t1": 204, "tn": 5098},   # rtt 4: offset 4896
                   {"k": "m", "n": "x:tap", "t": 300, "src": "js"})
        self.assertEqual((tr["clock"]["js"], tr["marks"]), ({"offset_ms": 4896.0, "error_ms": 2.0}, [(5196.0, "x:tap")]))

    def test_unsynced_unclosed_and_orphan_events_are_errors_not_silence(self):
        tr = trace({"k": "m", "n": "x:tap", "t": 1, "src": "js"}, b(1, 0, "data"), {"k": "e", "id": 9, "t": 5}, {"k": "m", "n": "y", "t": 20})
        self.assertEqual(tr["marks"], [(20, "y")])
        self.assertEqual(tr["spans"][0]["t1"], 20)
        self.assertEqual(sorted(e.split(":")[0] for e in tr["errors"]), ["end without begin", "unclosed span 1 s1", "unsynced clock"])

    def test_settled_needs_a_quiet_window(self):
        from orbit import perf
        tr = trace({"k": "busy", "th": "main", "t": 100, "d": 30}, {"k": "busy", "th": "main", "t": 160, "d": 10}, {"k": "m", "n": "end", "t": 400})
        self.assertEqual(perf.settled(tr, 100), 170)  # 130->160 is only 30ms quiet
        self.assertIsNone(perf.settled(trace({"k": "busy", "th": "main", "t": 100, "d": 30}), 100))  # trace ends before 50ms of quiet

    def test_mismatched_device_refuses_speedups(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(FIXTURE / "perf", Path(d) / "perf")
            f = Path(d) / "perf/next/rep-1.jsonl"
            f.write_text(f.read_text().replace('"iPhone 17 Pro"', '"iPhone SE"', 1))
            _, o, _ = jorbit("perf", str(Path(d) / "perf"))
            self.assertFalse(o["comparable"])
            self.assertTrue(o["refused"][0].startswith("meta.device differs"))
            self.assertFalse(any("speedup" in w for w in o["windows"]))

    def test_zst_traces_read_the_same(self):
        try:
            from compression import zstd
        except ImportError:
            self.skipTest("Python < 3.14")
        with tempfile.TemporaryDirectory() as d:
            for f in (FIXTURE / "perf").rglob("*.jsonl"):
                out = Path(d) / f.relative_to(FIXTURE / "perf")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix(".jsonl.zst").write_bytes(zstd.compress(f.read_bytes()))
            a, b_ = jorbit("perf", str(FIXTURE / "perf"))[1], jorbit("perf", d)[1]
            strip = lambda o: json.loads(json.dumps(o).replace(".jsonl.zst", ".jsonl"))
            self.assertEqual(strip(b_), a)

    def test_html_is_self_contained_and_byte_stable(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("a", "b"):
                orbit("perf", "tests/fixture/perf", "--html", str(Path(d) / f"{name}.html"))
            a, b_ = (Path(d) / "a.html").read_text(), (Path(d) / "b.html").read_text()
            self.assertEqual(a, b_)
            self.assertNotRegex(a, r"<script|https?://")
