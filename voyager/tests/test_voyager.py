"""Voyager-1 golden tests: a fixture repo with a real rounding bug, a scripted worker, every check and transition.

  python3 voyager.py selftest      (needs a python with pytest: ~/.voyager/selftest/venv, created with uv if missing)
"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import vy, voyager, validate as V, mutate, constraints as C  # noqa: E402

VENV = Path.home() / '.voyager' / 'selftest' / 'venv'
MONEY = 'def round_pence(x):\n    """Round an amount in pounds to whole pence, half up."""\n    return int(x * 100) / 100\n'
TEST = 'from money import round_pence\n\n\ndef test_whole_amount_unchanged():\n    """2.00 stays 2.00"""\n    assert round_pence(2.0) == 2.0\n'


def py():
    if not (VENV / 'bin' / 'python').exists():
        subprocess.run(['uv', 'venv', '-q', str(VENV)], check=True)
        subprocess.run(['uv', 'pip', 'install', '-q', '--python', str(VENV / 'bin' / 'python'), 'pytest'], check=True)
    return str(VENV / 'bin' / 'python')


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='voyager-selftest-'))
        vy.STATE = cls.tmp / 'state'
        repo = cls.repo = cls.tmp / 'vyfixture'
        (repo / 'pkg' / 'tests').mkdir(parents=True)
        (repo / 'pkg' / 'money.py').write_text(MONEY)
        (repo / 'pkg' / 'conftest.py').write_text('')
        (repo / 'pkg' / 'tests' / 'test_money.py').write_text(TEST)
        (repo / '.voyager').mkdir()
        (repo / '.voyager' / 'profile.toml').write_text(f'''name = "vyfixture"
runner = "pytest"
python = "{py()}"
test_cwd = "pkg"
sparse = ["pkg"]
test_patterns = ['(^|/)tests?/.*test_[^/]*\\.py$']
protected = ["pkg/conftest.py", ".voyager/"]
source_ext = [".py"]
''')
        sh(repo, 'git', 'init', '-q', '-b', 'main')
        sh(repo, 'git', 'add', '-A')
        sh(repo, 'git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'base')
        cls.remote = cls.tmp / 'remote.git'
        sh(cls.tmp, 'git', 'init', '-q', '--bare', str(cls.remote))
        sh(repo, 'git', 'remote', 'add', 'origin', str(cls.remote))
        cls.prof = vy.profile(repo)
        cls.base = sh(repo, 'git', 'rev-parse', 'HEAD')
        os.environ['VOYAGER_FAKE_CLAUDE'] = str(HERE / 'fake_claude.py')
        os.environ['VOYAGER_FAKE_LOG'] = str(cls.tmp / 'fake.log')
        os.environ.setdefault('GIT_AUTHOR_NAME', 't')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def job(self, fake, jid, **kw):
        os.environ['VOYAGER_FAKE_MODE'] = fake
        a = type('A', (), dict(id=jid, goal='round_pence truncates: 12.5p comes out as 12p. Amounts must round half up.',
                               title=None, accept=['0.125 -> 0.13', '2.00 stays 2.00'], base=None, mode='factory',
                               pr='local', hidden='writer', ui=None, mutation_floor=0.5, retries=2, turns=10, minutes=5,
                               gate=None, gate_timeout=None))
        for k, v in kw.items():
            setattr(a, k, v)
        return voyager.Job(self.prof, voyager.new_job(self.prof, a))

    def checks(self, j, attempt=0):
        ev = vy.jload(j.dir / f'validate-{attempt}.json')
        return {r['check']: r['pass'] for r in ev['checks']}

    # --- the three proofs, in miniature
    def test_good_fix_passes_every_check_and_lands_as_pr_body(self):
        j = self.job('good', 'good')
        s = j.run()
        self.assertEqual(s['step'], 'done')
        self.assertNotIn('escalated', s)
        self.assertTrue(all(self.checks(j).values()), self.checks(j))
        ev = vy.jload(j.dir / 'evidence.json')
        self.assertIn('test_half_pence_rounds_up', json.dumps(ev['validate']))
        self.assertIn('| f2p | pass', (j.dir / 'PR.md').read_text())

    def test_seeded_wrong_fix_is_rejected_by_the_hidden_scenarios(self):
        j = self.job('wrong', 'wrong')
        s = j.run()
        self.assertTrue(s.get('escalated'), 'the wrong fix must never reach done')
        self.assertRegex(s['escalated'], 'out of retries|retry made no new commit')
        c = self.checks(j)
        self.assertTrue(c['f2p'], 'the wrong fix passes its own test: exactly the plausible-but-wrong case')
        self.assertFalse(c['hidden'])

    def test_wrong_fix_gets_feedback_and_the_retry_is_accepted(self):
        j = self.job('wrong-then-good', 'retry')
        s = j.run()
        self.assertNotIn('escalated', s)
        self.assertEqual(s['attempt'], 1)
        self.assertFalse(self.checks(j, 0)['hidden'])
        self.assertTrue(all(self.checks(j, 1).values()))

    def test_killed_job_resumes_its_session_without_a_second_branch(self):
        os.environ['VOYAGER_FAKE_MODE'] = 'slow'
        j = self.job('slow', 'crash')
        env = {**os.environ, 'VOYAGER_STATE': str(vy.STATE), 'PYTHONPATH': str(HERE.parent)}
        p = subprocess.Popen([sys.executable, str(HERE.parent / 'voyager.py'), 'job', 'run', 'crash', '--repo', str(self.repo)],
                             env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(300):  # until the builder has committed its test
            if vy.jload(j.dir / 'state.json', {}).get('step') == 'build' and \
                    'test: half pence' in vy.git(j.build_wt, 'log', '-1', '--format=%s', check=False):
                break
            time.sleep(0.2)
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        s = voyager.Job(self.prof, 'crash').run()
        self.assertEqual(s['step'], 'done')
        self.assertNotIn('escalated', s)
        builds = [x for x in s['sessions'] if x['name'].startswith('build')]
        self.assertEqual([b['resumed'] for b in builds], [True], 'the interrupted session is continued, not restarted')
        branches = vy.git(vy.bare(self.prof), 'branch', '--list', '--format=%(refname:short)', 'voyager/crash*').split()
        self.assertEqual(branches, ['voyager/crash'])

    def test_pr_step_never_opens_a_second_pr_for_the_same_branch(self):
        bindir = self.tmp / 'bin'
        bindir.mkdir(exist_ok=True)
        calls = self.tmp / 'gh-calls'
        (bindir / 'gh').write_text(f'''#!/bin/sh
echo "$@" >> {calls}
case "$2" in
  list) [ -f {calls}.made ] && echo '[{{"url":"https://example/pr/1"}}]' || echo '[]' ;;
  create) touch {calls}.made; echo https://example/pr/1 ;;
esac
''')
        (bindir / 'gh').chmod(0o755)
        os.environ['PATH'] = f"{bindir}:{os.environ['PATH']}"
        sh(self.repo, 'git', 'push', '-q', str(self.remote), 'HEAD:main')   # the base is published
        j = self.job('good', 'pr', pr='draft')
        s = j.run()
        self.assertEqual(s['pr']['url'], 'https://example/pr/1')
        j2 = voyager.Job(self.prof, 'pr')   # a crash after create, before the state was saved: redo the step
        j2.s['step'] = 'pr'
        j2.run()
        self.assertEqual(calls.read_text().count('pr create'), 1)
        self.assertIn('voyager/pr', sh(self.tmp, 'git', '--git-dir', str(self.remote), 'branch'))

    def test_pr_step_refuses_to_publish_unpushed_base_commits(self):
        sh(self.repo, 'git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'local only')
        vy.git(vy.bare(self.prof), 'fetch', '-q', str(self.repo), '+refs/heads/main:refs/heads/main')
        j = self.job('good', 'unpushed', pr='draft', base=sh(self.repo, 'git', 'rev-parse', 'HEAD'))
        s = j.run()
        self.assertIn('not on main', s['escalated'])
        sh(self.repo, 'git', 'reset', '-q', '--hard', 'HEAD~1')

    def test_api_outage_pauses_the_job_instead_of_scoring_it(self):
        j = self.job('apierror', 'outage')
        with self.assertRaises(vy.InfraError):
            j.run()
        s = vy.jload(j.dir / 'state.json')
        self.assertNotIn('escalated', s, 'an outage is not the agent failing')
        self.assertEqual(s['step'], 'scenarios')
        os.environ['VOYAGER_FAKE_MODE'] = 'good'
        s = voyager.Job(self.prof, 'outage').run()   # network back: the same command finishes the job
        self.assertEqual(s['step'], 'done')
        self.assertNotIn('escalated', s)

    def test_a_gate_that_never_ran_pauses_instead_of_failing_the_candidate(self):
        # under load 300+ a gate suite timed out on base and candidate, read as "gates failed", and a builder spent
        # an hour fixing tests that were never broken (P1, 2026-10-26)
        j = self.job('good', 'gate-never-ran', gate=['pkg/tests/test_does_not_exist.py'])
        with self.assertRaises(vy.InfraError):
            j.run()
        s = vy.jload(j.dir / 'state.json')
        self.assertEqual(s['step'], 'validate')
        self.assertNotIn('escalated', s)
        self.assertEqual(s['attempt'], 0, 'the builder must not be sent to fix a gate that never ran')

    def test_review_mode_is_the_default_and_a_flagged_risk_escalates_without_a_loop(self):
        # the pre-registered recommendation: one-shot + diff-only reviewer (docs/prereg-backtest-2026-10-25.md)
        self.assertIn("default='review'", (HERE.parent / 'voyager.py').read_text())
        good = self.job('good', 'review-good', mode='review')
        s = good.run()
        self.assertNotIn('escalated', s)
        self.assertFalse((good.dir / 'validate-0.json').exists(), 'review mode runs no validator')
        self.assertEqual(vy.jload(good.dir / 'review-0.json')['verdict'], 'pass')
        wrong = self.job('wrong', 'review-wrong', mode='review')
        s = wrong.run()
        self.assertIn('reviewer revise', s['escalated'])
        self.assertEqual(s['attempt'], 0, 'review mode flags for a person; it does not retry')

    def test_unconfirmed_held_out_scenarios_are_evidence_not_a_veto(self):
        # backtest: 3 escalations came from held-out tests that rejected fixes the real fix's tests accept
        os.environ['VOYAGER_FAKE_CONFIRM'] = 'none'
        try:
            j = self.job('wrong-then-good', 'disputed')
            j.run()
        finally:
            os.environ['VOYAGER_FAKE_CONFIRM'] = 'all'
        hidden = next(r for r in vy.jload(j.dir / 'validate-0.json')['checks'] if r['check'] == 'hidden')
        self.assertTrue(hidden['pass'], 'a disputed scenario must not veto on its own')
        self.assertTrue(hidden['detail']['disputed'])
        self.assertEqual(vy.jload(j.dir / 'review-0.json')['verdict'], 'revise', 'the reviewer still sees it')

    # --- validator checks one by one
    def test_weakened_test_is_caught(self):
        j = self.job('weaken', 'weaken', retries=0)
        j.run()
        c = self.checks(j)
        self.assertFalse(c['weakening'])
        self.assertFalse(c['f2p'])

    def test_fix_without_a_failing_test_is_caught(self):
        j = self.job('notest', 'notest', retries=0, hidden='none')
        j.run()
        self.assertFalse(self.checks(j)['f2p'])

    def test_oneshot_mode_skips_the_factory(self):
        j = self.job('good', 'oneshot', mode='oneshot')
        s = j.run()
        self.assertEqual(s['step'], 'done')
        self.assertFalse((j.dir / 'validate-0.json').exists())
        roles = [json.loads(l)['role'] for l in (self.tmp / 'fake.log').read_text().splitlines()]
        self.assertIn('build', roles)

    # --- instruments
    def test_mutants_cover_changed_lines_only(self):
        src = 'def f(a, b):\n    if a < b:\n        return a + 1\n    return b\n'
        lines2 = {d for ln, d, _ in mutate.mutants(src, {2})}
        self.assertEqual(lines2, {'compare flipped', 'condition negated'})
        self.assertTrue(all(ln == 3 for ln, _, _ in mutate.mutants(src, {3})))
        descs = {d for _, d, _ in mutate.mutants('def g(u):\n    s = str(u)\n    return s\n', {2})}
        self.assertIn('str(...) unwrapped', descs, 'the pilot fix str(uuid) had no mutant at all')

    def test_constraint_delta_reports_added_and_weakened(self):
        j = self.job('weaken', 'delta-src', retries=0)
        j.run()
        d = C.delta(self.prof, j.judge_wt, self.base, j.s['cand'])
        self.assertTrue(any('test_whole_amount_unchanged asserts 1 -> 0' in w for w in d['weakened']), d)


if __name__ == '__main__':
    unittest.main()
