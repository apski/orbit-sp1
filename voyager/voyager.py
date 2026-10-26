#!/usr/bin/env python3
"""Voyager-1: the layer above Orbit that turns a job into a validated, PR-able piece of work.

  voyager env up|down [--wipe]                 the adapter's env fixtures (e.g. its own throwaway Postgres)
  voyager job new --goal TEXT [--accept TEXT]... [--base SHA] [--mode review|oneshot|factory] [--pr draft|local] [--ui APP]
                  modes (verification levels): review (default) = one-shot builder + diff-only reviewer, escalate on a
                  flagged risk; oneshot = builder only; factory = held-out scenarios + protected validator + reviewer +
                  retries. The 2026-10-25 backtest found factory too costly and too quick to reject correct fixes on
                  small tested bugs (docs/prereg-backtest-2026-10-25.md): use it where it has a case (UI, multi-file).
                  [--cand SHA]                 judge an existing change (no builder): validator + reviewer + record
  voyager job run <id>                         run (or resume) the state machine; safe to re-run after a crash
  voyager job status [<id>]
  voyager validate --base SHA --cand SHA       the protected validator alone, evidence JSON on stdout
  voyager constraints map <path>... | delta <base> <cand>
  voyager strengthen <src file> [--tests t1,t2] the strengthening loop on one unit (see strengthen.py)
  voyager backtest ...                         one-shot vs factory on the repo's own history (see backtest.py)
  voyager selftest                             golden test: a fixture repo + a scripted worker, every check

Every command takes --repo <path> (default: the nearest dir with .voyager/profile.toml) or --profile <example name>.
Job state lives outside the repo: ~/.voyager/<name>/jobs/<id>/ (job.json contract, state.json, events.jsonl,
evidence.json, logs/, hidden/, recording/).

States: workspace -> scenarios -> build -> validate -> review -> record -> evidence -> pr -> done
        (validate/review failures loop back to build with feedback, up to budget.retries; then escalated)
"""
import argparse, json, os, re, signal, sys, time, uuid
from pathlib import Path
import vy, validate as V, constraints as C, env as E

PROMPTS = vy.HOME / 'prompts'
REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'reason', 'evidence'],
                 'properties': {'verdict': {'type': 'string', 'enum': ['pass', 'revise', 'reject']},
                                'reason': {'type': 'string'}, 'evidence': {'type': 'string'}}}
STEPS = ['workspace', 'scenarios', 'build', 'validate', 'review', 'record', 'evidence', 'pr', 'done']


def render(name, **kw):
    t = (PROMPTS / name).read_text()
    for k, v in kw.items():
        t = t.replace('{{%s}}' % k, str(v))
    return t


def test_cmd(prof):
    return f"{prof['python']} -m pytest -q " + ' '.join(prof.get('pytest_args', []))


class Job:
    def __init__(self, prof, jid):
        self.prof, self.id = prof, jid
        self.dir = Path(prof['home']) / 'jobs' / jid
        self.c = vy.jload(self.dir / 'job.json')
        if not self.c:
            raise SystemExit(f'no job {jid}')
        self.s = vy.jload(self.dir / 'state.json', {'step': 'workspace', 'attempt': 0, 'cost_usd': 0.0,
                                                     'sessions': [], 'feedback': None})
        self.build_wt = Path(prof['home']) / 'wt' / f'{jid}-build'
        self.judge_wt = Path(prof['home']) / 'wt' / f'{jid}-judge'
        (self.dir / 'logs').mkdir(parents=True, exist_ok=True)

    # --- bookkeeping: every transition is persisted before the next step starts
    def event(self, kind, **kw):
        with open(self.dir / 'events.jsonl', 'a') as f:
            f.write(json.dumps({'t': time.strftime('%FT%T'), 'job': self.id, 'kind': kind, **kw}, default=str) + '\n')

    def save(self):
        vy.jdump(self.dir / 'state.json', self.s)

    def goto(self, step, **kw):
        self.s['step'] = step
        self.s.update(kw)
        self.save()
        self.event('step', step=step, **{k: v for k, v in kw.items() if k != 'feedback'})

    def session(self, name, prompt, cwd, **kw):
        """A claude session whose id is fixed before it starts, so a crash mid-session can be resumed."""
        key = f'{name}-{self.s["attempt"]}'
        sid = self.s.setdefault('sids', {}).get(key)
        resume = None
        if sid and (self.dir / 'logs' / f'{key}.started').exists():
            resume = sid  # the previous run died inside this session: continue it, don't start a second one
        elif sid:
            sid = None    # that session ended (a reopened or reset job reached this key again): a fresh one
        if not sid:
            sid = str(uuid.uuid4())
            self.s['sids'][key] = sid
            self.save()
        (self.dir / 'logs' / f'{key}.started').touch()
        if resume:
            prompt = 'You were interrupted. Continue the task from where you stopped; check git log and the tree first.'
        out = vy.claude(prompt, cwd, self.dir / 'logs' / f'{key}.json', session_id=None if resume else sid,
                        resume=resume, **kw)
        (self.dir / 'logs' / f'{key}.started').unlink(missing_ok=True)
        cost = float(out.get('total_cost_usd') or 0)
        self.s['cost_usd'] = round(self.s['cost_usd'] + cost, 4)
        self.s['sessions'].append({'name': key, 'id': sid, 'rc': out.get('rc'), 'secs': out.get('secs'),
                                   'cost_usd': cost, 'turns': out.get('num_turns'), 'resumed': bool(resume)})
        self.save()
        return out

    def env_extra(self):
        return {**self.prof.get('test_env', {})}

    # --- steps
    def workspace(self):
        sp = self.c.get('sparse')  # a job can widen the checkout (e.g. a UI lane's app dir)
        vy.worktree(self.prof, self.c['base'], self.build_wt, branch=self.c['branch'], sparse=sp)
        vy.worktree(self.prof, self.c['base'], self.judge_wt, sparse=sp)
        self.goto('scenarios')

    def scenarios(self):
        if self.c['mode'] != 'factory' or self.c.get('hidden') == 'none':
            return self.goto('build')
        hfile = f"{self.prof['test_cwd']}/tests/test_voyager_holdout_{self.id.replace('-', '_')}.py"
        if not (self.dir / 'hidden' / 'files.json').exists():
            wt = Path(self.prof['home']) / 'wt' / f'{self.id}-scen'
            vy.worktree(self.prof, self.c['base'], wt)
            vy.git(wt, 'checkout', '-q', '-f', self.c['base'])
            self.session('scenarios', render('scenarios.md', WT=wt, TEST_CWD=self.prof['test_cwd'],
                                             TEST_CMD=test_cmd(self.prof), GOAL=self.c['goal'],
                                             ACCEPTANCE=self.acceptance(), HIDDEN_FILE=hfile),
                         wt, max_turns=40, timeout=1200, disallowed=vy.WORKER_DENY, env_extra=self.env_extra())
            text = (wt / hfile).read_text() if (wt / hfile).exists() else ''
            res = vy.pytest(self.prof, wt, [hfile])[0] if text else {}
            vy.drop_worktree(self.prof, wt)
            vy.jdump(self.dir / 'hidden' / 'files.json', {'files': {hfile: text} if text else {}, 'run': [hfile],
                                                          'tests': sorted(res), 'on_base': res})
        h = vy.jload(self.dir / 'hidden' / 'files.json')
        if h['files'] and 'veto' not in h:
            h['veto'], h['confirm'] = confirm_scenarios(self, h)
            vy.jdump(self.dir / 'hidden' / 'files.json', h)
        self.event('scenarios', n=len(h['tests']), fail_on_base=sum(v != 'pass' for v in h['on_base'].values()),
                   veto=len(h.get('veto', [])))
        self.goto('build')

    def after_build(self):
        return {'factory': 'validate', 'review': 'review'}.get(self.c['mode'], 'evidence')

    def acceptance(self):
        a = self.c.get('acceptance') or []
        return ('## Acceptance\n\n' + '\n'.join(f'- {x}' for x in a)) if a else ''

    def build(self):
        if self.c.get('cand_given'):
            # judging an existing change (a person's branch, another agent's): no builder, nothing to retry with
            if self.s['attempt'] > 0:
                return self.escalate(f"given candidate not accepted: {self.s.get('feedback', '')[:500]}")
            self.s['cand'] = self.c['cand_given']
            return self.goto(self.after_build(), cand=self.c['cand_given'])
        fb = self.s.get('feedback')
        tmpl = 'worker.md' if self.c['mode'] == 'factory' else 'oneshot.md'   # review uses the one-shot brief
        prompt = render(tmpl, REPO_NAME=self.prof['name'], WT=self.build_wt, BRANCH=self.c['branch'], GOAL=self.c['goal'],
                        ACCEPTANCE=self.acceptance(), PROTECTED=', '.join(self.prof.get('protected', [])),
                        TEST_CWD=self.prof['test_cwd'], TEST_CMD=test_cmd(self.prof))
        if fb:
            prompt = (f'{prompt}\n\n## Previous attempt was NOT accepted\n\n{fb}\n\nFix this on the same branch, '
                      f'commit, and end with RESULT: done|blocked.')
        out = self.session('build', prompt, self.build_wt, max_turns=self.c['budget']['worker_turns'],
                           timeout=self.c['budget']['worker_minutes'] * 60, disallowed=vy.WORKER_DENY,
                           env_extra=self.env_extra())
        head = vy.git(self.build_wt, 'rev-parse', 'HEAD')
        dirty = vy.git(self.build_wt, 'status', '--porcelain', '--untracked-files=no')
        if out.get('rc') == 124 and head in (self.c['base'], self.s.get('cand')) and dirty and self.c.get('wrapup'):
            # out of time with work on disk: one short wrap-up turn in the same session instead of losing it
            sid = self.s['sids'][f"build-{self.s['attempt']}"]
            self.event('wrapup', dirty=dirty.count('\n') + 1)
            out = vy.claude('Time is up. Run your reproducing test once. If it passes, commit your change now '
                            '(pathspec commit) and end with RESULT: done. If it does not, leave it uncommitted and end '
                            'with RESULT: blocked <what is left>.', self.build_wt, self.dir / 'logs' / f"wrapup-{self.s['attempt']}.json",
                            resume=sid, max_turns=15, timeout=600, disallowed=vy.WORKER_DENY, env_extra=self.env_extra())
            self.s['cost_usd'] = round(self.s['cost_usd'] + float(out.get('total_cost_usd') or 0), 4)
            head = vy.git(self.build_wt, 'rev-parse', 'HEAD')
        text = out.get('result') or ''
        blocked = re.search(r'RESULT:\s*blocked(.*)', text)
        self.s['feedback'] = None
        if head == self.c['base']:
            return self.escalate('builder made no commit' + (f': {blocked.group(1).strip()}' if blocked else ''))
        if self.s['attempt'] > 0 and head == self.s.get('cand'):
            # a retry that committed nothing new would only re-validate the same commit: stop here
            return self.escalate('retry made no new commit' + (f': {blocked.group(1).strip()}' if blocked else ''))
        self.s['cand'] = head
        self.s['builder_says'] = 'blocked' if blocked else 'done'
        self.goto(self.after_build(), cand=head)

    def validate(self):
        h = vy.jload(self.dir / 'hidden' / 'files.json')
        ev = V.validate(self.prof, self.judge_wt, self.c['base'], self.s['cand'],
                        hidden=h if h and h['files'] else None, mutation_floor=self.c.get('mutation_floor', 0.5),
                        gates=self.c.get('gates'), f2p_required=not self.c.get('ui'),
                        gate_timeout=self.c.get('gate_timeout'))
        vy.jdump(self.dir / f"validate-{self.s['attempt']}.json", ev)
        self.event('validate', verdict=ev['verdict'], failed=[r['check'] for r in ev['checks'] if not r['pass']])
        if ev['verdict'] == 'pass':
            return self.goto('review')
        self.retry('validator: ' + V.feedback(ev))

    def retry(self, feedback):
        if self.s['attempt'] >= self.c['budget']['retries']:
            return self.escalate(f'out of retries; last: {feedback[:500]}')
        self.s['attempt'] += 1
        self.goto('build', feedback=feedback)

    def review(self):
        base, cand = self.c['base'], self.s['cand']
        vy.git(self.judge_wt, 'checkout', '-q', '-f', cand)
        diff = vy.git(self.judge_wt, 'diff', base, cand)
        if len(diff.splitlines()) > 600:
            diff = vy.git(self.judge_wt, 'diff', '--stat', base, cand) + '\n(diff too long: read files with git show)'
        ev = vy.jload(self.dir / f"validate-{self.s['attempt']}.json")
        if ev:
            summary = '\n'.join(f"- {r['check']}: {'pass' if r['pass'] else 'FAIL'}" + (
                f" (disputed held-out scenarios failed, evidence only: {r['detail']['disputed']})"
                if r['detail'].get('disputed') else '') for r in ev['checks'])
        else:
            summary = f"No validator ran (mode {self.c['mode']}): judge the diff against the job yourself."
        out = self.session('review', render('reviewer.md', GOAL=self.c['goal'], ACCEPTANCE=self.acceptance(),
                                            EVIDENCE=summary, DELTA=C.delta_md(C.delta(self.prof, self.judge_wt, base, cand)),
                                            BASE=base[:10], CAND=cand[:10], DIFF=diff, WT=self.judge_wt),
                           self.judge_wt, max_turns=30, timeout=900, schema=REVIEW_SCHEMA,
                           allowed=['Read', 'Grep', 'Glob', 'Bash(git *)'])
        v = out.get('structured_output') or {'verdict': 'revise', 'reason': 'reviewer returned no verdict', 'evidence': ''}
        vy.jdump(self.dir / f"review-{self.s['attempt']}.json", v)
        self.event('review', **v)
        if v['verdict'] == 'pass':
            return self.goto('record')
        if v['verdict'] == 'reject' or self.c['mode'] == 'review':
            # review mode: the reviewer is a flag for a person, not a loop (escalate on a specific risk)
            return self.escalate(f"reviewer {v['verdict']}: {v['reason']}")
        self.retry(f"reviewer: {v['reason']} ({v['evidence']})")

    def record(self):
        """UI jobs: walk every scenario on screen, on the base build (must show the problem: the UI's fail-before)
        and on the candidate build (every scenario must pass). Both are argent screen recordings."""
        ui = self.c.get('ui')
        if not ui:
            self.s['recording'] = {'status': 'n/a', 'why': 'job declares no UI surface'}
            return self.goto('evidence')
        lane = self.prof.get('lanes', {}).get(ui.get('lane', ''), {})
        rec = {}
        lease = None
        mode = (Path.home() / '.argent' / 'sim-mode').read_text().strip() if (Path.home() / '.argent' / 'sim-mode').exists() else 'slim'
        if ui.get('udid', 'fleet') == 'fleet' and mode == 'slim' and vy.sh(['sh', '-c', 'command -v orbitfleet'])[0] == 0:
            # one agent, one simulator: lease one from orbitfleet, never a booted sim someone else is using
            rc, out = vy.sh(['orbitfleet', 'claim', '--holder', f"voyager:{self.id}", '--purpose', 'voyager recording',
                             '--wait', '10m', '--ttl', '120m', '--json'], timeout=700)
            try:
                lease = json.loads(out[out.index('{'):])
            except ValueError:
                return self.escalate(f'orbitfleet claim failed: {out[-300:]}')
            ui = {**ui, 'udid': lease['udid']}
        try:
            return self._record(ui, lane, rec)
        finally:
            if lease:
                vy.sh(['orbitfleet', 'release', lease['udid'], '--holder', f"voyager:{self.id}"])

    def _record(self, ui, lane, rec):
        for phase, sha in (('before', self.c['base']), ('after', self.s['cand'])):
            out_dir = self.dir / 'recording' / (phase if phase == 'before' else f"after-{self.s['attempt']}")
            out_dir.mkdir(parents=True, exist_ok=True)
            app_path = ''
            if lane.get('build'):
                vy.git(self.judge_wt, 'checkout', '-q', '-f', sha)
                dd = Path(self.prof['home']) / 'dd' / self.id   # shared: the second build is incremental
                t0 = time.time()
                rc, out = vy.sh(lane_cmd(lane['build'], dd), cwd=self.judge_wt, timeout=lane.get('build_timeout', 2400))
                self.event('ui-build', phase=phase, secs=round(time.time() - t0), rc=rc)
                (self.dir / 'logs' / f'ui-build-{phase}.log').write_text(out)
                app_path = lane['app'].format(dd=dd)
                if rc or not Path(app_path).exists():
                    return self.escalate(f'UI build ({phase}) failed (rc {rc}): logs/ui-build-{phase}.log')
            if not (out_dir / 'walkthrough.json').exists():
                self.session(f'record-{phase}', render('recorder.md', APP=ui['app'], APP_PATH=app_path or '(already installed)',
                                                       UDID=ui['udid'], OUT=out_dir,
                                                       LOGIN=ui.get('login', 'the app is already signed in on this simulator'),
                                                       SCENARIOS='\n'.join(f'{i + 1}. {x}' for i, x in enumerate(self.c['acceptance']))),
                             self.build_wt, max_turns=120, timeout=2400, mcp=vy.HOME / 'mcp-argent.json')
            walk = vy.jload(out_dir / 'walkthrough.json', [])
            rec[phase] = {'videos': sorted(str(p) for p in out_dir.glob('*.mp4') if p.stat().st_size > 0), 'walkthrough': walk}
        n = len(self.c['acceptance'])
        blocked = lambda w: str(w.get('seen', '')).upper().startswith('BLOCKED') or w.get('blocked')
        stuck = [w['scenario'] for ph in ('before', 'after') for w in rec.get(ph, {}).get('walkthrough', []) if blocked(w)]
        if stuck:
            # the environment stopped the walk (sign-in, a missing service): no builder can fix that, never retry it
            self.s['recording'] = {**rec, 'status': 'blocked'}
            self.save()
            return self.escalate(f'recording blocked by the environment, not the change ({len(stuck)} scenario walks): '
                                 + str(rec['before']['walkthrough'][0].get('seen', ''))[:300])
        before_shows_bug = any(not w.get('pass') for w in rec['before']['walkthrough'])
        after_ok = bool(rec['after']['videos']) and len(rec['after']['walkthrough']) >= n and \
            all(w.get('pass') for w in rec['after']['walkthrough'])
        rec['status'] = 'pass' if after_ok and before_shows_bug else 'fail'
        rec['before_shows_bug'] = before_shows_bug
        self.s['recording'] = rec
        self.save()
        if not before_shows_bug:
            return self.escalate('recording on the base build shows no failing scenario: the scenarios do not capture the bug')
        if not after_ok:
            return self.retry(f"recording: not every scenario passed on screen: {rec['after']['walkthrough']}")
        self.goto('evidence')

    def evidence(self):
        cand = self.s['cand']
        ev = {'job': self.c, 'base': self.c['base'], 'candidate': cand, 'branch': self.c['branch'],
              'mode': self.c['mode'], 'attempts': self.s['attempt'] + 1, 'cost_usd': self.s['cost_usd'],
              'sessions': self.s['sessions'], 'builder_says': self.s.get('builder_says'),
              'validate': vy.jload(self.dir / f"validate-{self.s['attempt']}.json"),
              'review': vy.jload(self.dir / f"review-{self.s['attempt']}.json"),
              'hidden': (vy.jload(self.dir / 'hidden' / 'files.json') or {}).get('on_base'),
              'recording': self.s.get('recording'),
              'delta': C.delta(self.prof, self.judge_wt, self.c['base'], cand), 'voyager': vy.git(vy.HOME, 'rev-parse', '--short', 'HEAD', check=False)}
        vy.jdump(self.dir / 'evidence.json', ev)
        (self.dir / 'PR.md').write_text(pr_body(ev))
        self.goto('pr')

    def pr(self):
        if self.c['pr'] != 'draft':
            return self.goto('done', pr={'kind': 'local', 'body': str(self.dir / 'PR.md')})
        src = self.prof['source']
        url = vy.git(src, 'remote', 'get-url', 'origin')
        br = self.c['branch']
        # never publish someone's unpushed work: the job's base must already be on the remote's PR base branch
        b = vy.bare(self.prof)
        try:
            pr_base = self.c.get('pr_base') or vy.git(b, 'ls-remote', '--symref', url, 'HEAD', timeout=120).split()[1].removeprefix('refs/heads/')
            vy.git(b, 'fetch', '-q', url, f'+refs/heads/{pr_base}:refs/voyager/remote-base', timeout=600)
        except (RuntimeError, IndexError) as e:
            return self.escalate(f'cannot read the PR base branch from {url}: {str(e)[:300]}')
        unpublished = int(vy.git(b, 'rev-list', '--count', f"refs/voyager/remote-base..{self.c['base']}"))
        if unpublished:
            return self.escalate(f'base {self.c["base"][:10]} has {unpublished} commits not on {pr_base} at {url}: '
                                 f'a PR would publish them. Re-base the job on a published commit (--base) or push them first.')
        self.c['pr_base'] = pr_base
        # idempotent: an existing PR for this head (open or closed) is reused, never duplicated
        rc, out = vy.sh(['gh', 'pr', 'list', '--repo', gh_repo(url), '--head', br, '--state', 'all', '--json', 'url'], cwd=src)
        existing = json.loads(out or '[]') if rc == 0 else []
        if not existing:
            vy.git(vy.bare(self.prof), 'push', url, f"{self.s['cand']}:refs/heads/{br}", timeout=300)
            rc, out = vy.sh(['gh', 'pr', 'create', '--repo', gh_repo(url), '--draft', '--head', br,
                             '--base', pr_base, '--title', self.c['title'],
                             '--body-file', str(self.dir / 'PR.md')], cwd=src)
            if rc:
                return self.escalate(f'gh pr create failed: {out[-400:]}')
            existing = [{'url': out.strip().splitlines()[-1]}]
        self.goto('done', pr={'kind': 'github-draft', 'url': existing[0]['url']})

    def escalate(self, why):
        self.event('escalated', why=why)
        self.goto('done', escalated=why)

    def run(self):
        lock = self.dir / 'lock'
        if lock.exists():
            try:
                os.kill(int(lock.read_text()), 0)
                raise SystemExit(f'job {self.id} is already running (pid {lock.read_text()})')
            except (ProcessLookupError, ValueError):
                self.event('stale-lock', pid=lock.read_text())
        lock.write_text(str(os.getpid()))
        try:
            while self.s['step'] != 'done':
                step = self.s['step']
                self.event('enter', step=step)
                try:
                    getattr(self, step)()
                except vy.InfraError as e:
                    # not the agent's fault and not a result: stay at this step, `job run` resumes it later
                    self.event('paused-infra', step=step, why=str(e))
                    self.s['paused'] = f'infra: {e}'
                    self.save()
                    raise
        finally:
            lock.unlink(missing_ok=True)
        return self.s


CONFIRM_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['tests'], 'properties': {'tests': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['name', 'required', 'why'],
                               'properties': {'name': {'type': 'string'}, 'required': {'type': 'boolean'},
                                              'why': {'type': 'string'}}}}}}


def confirm_scenarios(job, h):
    """A second reader marks each held-out test required-by-the-job or not. Only confirmed tests may veto a change;
    the others are evidence for the reviewer. (Backtest: 3 escalations came from held-out tests that rejected fixes
    the real fix's tests accept.) Returns (veto test ids, the checker's verdicts)."""
    rel, text = next(iter(h['files'].items()))
    out = job.session('confirm', render('confirm.md', GOAL=job.c['goal'], ACCEPTANCE=job.acceptance(), HIDDEN_FILE=rel,
                                        TESTS=text[:12000], ON_BASE=json.dumps(h['on_base']), WT=job.judge_wt),
                      job.judge_wt, max_turns=15, timeout=600, schema=CONFIRM_SCHEMA, allowed=['Read', 'Grep', 'Glob'])
    verdicts = (out.get('structured_output') or {}).get('tests', [])
    req = {v['name'].split('::')[-1] for v in verdicts if v.get('required')}
    return sorted(t for t in h['tests'] if t.split('::')[-1] in req), verdicts


def lane_cmd(template, dd):
    """A UI lane's build command with the pack's build-speed overlay (perf/skills/xcode-build-speed): one shared
    compilation cache and SwiftPM clone dir for every job's worktree, so the base and candidate builds of every job
    compile each file once per machine instead of once per worktree. {jobs} caps parallelism for many agents."""
    return template.format(dd=dd, xcconfig=vy.HOME.parent / 'perf' / 'skills' / 'xcode-build-speed' / 'fast-sim.xcconfig',
                           packages=Path.home() / 'Library' / 'Developer' / 'Xcode' / 'SourcePackages.shared',
                           jobs=max(2, (os.cpu_count() or 8) // 4))


def gh_repo(url):
    m = re.search(r'github\.com[:/](.+?)(\.git)?$', url)
    return m.group(1) if m else url


def pr_body(ev):
    v = ev['validate'] or {'checks': []}
    rows = '\n'.join(f"| {r['check']} | {'pass' if r['pass'] else '**FAIL**'} | {short(r['detail'])} |" for r in v['checks'])
    rv = ev['review'] or {}
    rec = ev['recording'] or {}
    if rec.get('status') == 'n/a' or not rec:
        rec_line = f"n/a ({rec.get('why', 'not run')})"
    else:
        rec_line = (f"{rec['status']}: before (base build, must show the bug): " + ', '.join(rec['before']['videos']) +
                    '; after (candidate): ' + ', '.join(rec['after']['videos']) + '\n\n' + '\n'.join(
                        f"- {w.get('scenario')}: {'pass' if w.get('pass') else 'FAIL'} — {w.get('seen', '')}" for w in rec['after']['walkthrough']))
    return f"""{ev['job']['goal']}

## Evidence (Voyager-1, independent of the builder)

Base `{ev['base'][:10]}` -> candidate `{ev['candidate'][:10]}`, {ev['attempts']} attempt(s), ${ev['cost_usd']:.2f}.

| check | result | detail |
|---|---|---|
{rows}

**Reviewer:** {rv.get('verdict', 'n/a')}: {rv.get('reason', '')}

**Recording:** {rec_line}

## Constraint delta

{C.delta_md(ev['delta'])}

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""


def short(d):
    s = json.dumps(d, default=str)
    return (s[:160] + '…') if len(s) > 160 else s


def new_job(prof, a):
    jid = a.id or time.strftime('%m%d-') + uuid.uuid4().hex[:6]
    d = Path(prof['home']) / 'jobs' / jid
    if (d / 'job.json').exists():
        return jid
    base = a.base or vy.git(vy.bare(prof), 'rev-parse', 'HEAD')
    c = {'id': jid, 'goal': a.goal, 'title': a.title or a.goal.splitlines()[0][:70], 'acceptance': a.accept or [],
         'base': vy.git(vy.bare(prof), 'rev-parse', base), 'branch': f'voyager/{jid}', 'mode': a.mode, 'pr': a.pr,
         'hidden': a.hidden, 'ui': json.loads(a.ui) if a.ui else None, 'gates': getattr(a, 'gate', None),
         'sparse': getattr(a, 'sparse', None),
         'cand_given': vy.git(vy.bare(prof), 'rev-parse', a.cand) if getattr(a, 'cand', None) else None, 'mutation_floor': a.mutation_floor,
         'budget': {'retries': a.retries, 'worker_turns': a.turns, 'worker_minutes': a.minutes},
         'wrapup': getattr(a, 'wrapup', False), 'gate_timeout': getattr(a, 'gate_timeout', None)}
    vy.jdump(d / 'job.json', c)
    return jid


LAUNCHER = '''#!/bin/sh
# Voyager-1 for this repo (adapter: profile.toml next to this file). Finds the orbit-SP1 checkout at run time.
op=""
for d in "$ORBIT_HOME" "$HOME/code/orbit-sp1"; do
  [ -n "$d" ] && [ -f "$d/ORBIT-SP1.md" ] && { op="$d"; break; }
done
[ -n "$op" ] || { echo "orbit-SP1 checkout not found: clone it to ~/code/orbit-sp1 or set ORBIT_HOME" >&2; exit 127; }
here="$(cd "$(dirname "$0")/.." && pwd)"
exec "$op/voyager/bin/voyager" "$@" --repo "$here"
'''


def detect(repo):
    """A starter adapter from what the repo has: the test runner, where tests run from, judge machinery to protect."""
    repo = Path(repo)
    confs = sorted(c for c in repo.glob('**/conftest.py') if not any(x in c.parts for x in ('.venv', 'node_modules', 'site-packages')))
    roots = sorted({c.parent.parent if c.parent.name in ('tests', 'test') else c.parent for c in confs}, key=lambda p: len(p.parts))
    if not roots and not list(repo.glob('**/pytest.ini')):
        return None, 'no pytest suite found (other runners: add one at vy.pytest / profile runner)'
    root = roots[0] if roots else repo
    cwd = str(root.relative_to(repo)) if root != repo else '.'
    venv = next((str(v.relative_to(repo)) for v in [root / '.venv' / 'bin' / 'python', repo / '.venv' / 'bin' / 'python'] if v.exists()), None)
    prot = sorted({str(c.relative_to(repo)) for c in confs} | {str(d.relative_to(repo)) + '/' for d in root.glob('**/tests/*')
                   if d.is_dir() and d.name in ('golden', 'fixtures', 'data', 'snapshots')})
    lines = [f'# Voyager-1 adapter (detected {time.strftime("%F")}; review with the voyager skill, then commit).',
             f'name = "{repo.name}"', 'runner = "pytest"', f'sparse = ["{cwd}"]' if cwd != '.' else 'sparse = []',
             f'python = "{venv or "python3"}"', f'test_cwd = "{cwd}"', 'pytest_args = ["-p", "no:randomly"]',
             "test_patterns = ['(^|/)tests?/.*test_[^/]*\\.py$', '(^|/)tests?/.*_test\\.py$']",
             'protected = [' + ', '.join(f'"{x}"' for x in prot + ['.voyager/']) + ']', 'source_ext = [".py"]', '',
             '# [test_env] KEY = "value"   env for test runs (no real credentials: Voyager strips the environment)',
             '# [env.postgres] port, schemas, tables, ssl   a throwaway DB fixture (see env.py); seed_from in local.toml']
    return '\n'.join(lines) + '\n', f'detected pytest in {cwd}' + ('' if venv else ' (no venv found: set python)')


def install(repo, name=None):
    repo = Path(repo).resolve()
    d = repo / '.voyager'
    d.mkdir(exist_ok=True)
    (d / 'voyager').write_text(LAUNCHER)
    (d / 'voyager').chmod(0o755)
    prof = d / 'profile.toml'
    starter = vy.HOME / 'examples' / f'{name or repo.name}.toml'
    if prof.exists():
        note = 'profile present'
    elif starter.exists():
        prof.write_text(starter.read_text())
        note = f'profile from examples/{starter.name}'
    else:
        text, note = detect(repo)
        if text:
            prof.write_text(text)
    print(f"voyager  {'installed' if prof.exists() else 'FAILED'}  {note}; launcher .voyager/voyager")


def status(repo):
    repo = Path(repo).resolve()
    if not (repo / '.voyager' / 'profile.toml').exists():
        return print('voyager  not installed  next: orbit install --only voyager')
    prof = vy.profile(repo)
    jobs = list((Path(prof['home']) / 'jobs').glob('*/state.json'))
    done = sum(1 for j in jobs if vy.jload(j, {}).get('step') == 'done' and not vy.jload(j, {}).get('escalated'))
    nxt = 'voyager skill: review profile, `env up`, selftest, first job' if not jobs else 'online'
    print(f"voyager  {'online' if jobs else 'installed'}  {len(jobs)} jobs, {done} done  next: {nxt}")


def find_repo(start='.'):
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / '.voyager' / 'profile.toml').exists():
            return str(d)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--repo')
    ap.add_argument('--profile')
    ap.add_argument('--goal')
    ap.add_argument('--title')
    ap.add_argument('--accept', action='append')
    ap.add_argument('--base')
    ap.add_argument('--cand')
    ap.add_argument('--id')
    ap.add_argument('--mode', default='review', choices=['review', 'oneshot', 'factory'])
    ap.add_argument('--pr', default='local', choices=['draft', 'local'])
    ap.add_argument('--hidden', default='writer', choices=['writer', 'none'])
    ap.add_argument('--ui', help='JSON: {"app": bundle id, "udid": simulator}')
    ap.add_argument('--retries', type=int, default=2)
    ap.add_argument('--turns', type=int, default=80)
    ap.add_argument('--minutes', type=int, default=30)
    ap.add_argument('--mutation-floor', type=float, default=0.5)
    ap.add_argument('--gate', action='append', help='always-run suite for this job (repeatable)')
    ap.add_argument('--gate-timeout', type=int, help='seconds per gate run (default: profile gate_timeout, else 1800)')
    ap.add_argument('--sparse', action='append', help='dirs this job checks out (default: the profile sparse)')
    ap.add_argument('--wipe', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_intermixed_args()
    a.minutes_set = '--minutes' in sys.argv
    a.wrapup = True   # interactive jobs get the wrap-up turn; the backtest builds its jobs without it
    if a.cmd == 'install':
        return install(a.args[0] if a.args else a.repo or '.', a.profile)
    if a.cmd == 'status':
        return status(a.args[0] if a.args else a.repo or '.')
    if a.cmd == 'selftest':
        import unittest
        sys.path.insert(0, str(vy.HOME / 'tests'))
        sys.exit(not unittest.main(module='test_voyager', argv=['x'], exit=False).result.wasSuccessful())
    prof = vy.profile(a.profile or a.repo or find_repo() or sys.exit('no .voyager/profile.toml here; pass --repo or --profile'))
    if a.cmd == 'env':
        print('\n'.join(E.up(prof) if a.args[0] == 'up' else E.down(prof, a.wipe)))
    elif a.cmd == 'job':
        sub = a.args[0]
        if sub == 'new':
            print(new_job(prof, a))
        elif sub == 'reopen':
            # an escalated job continues where it stopped: back to build, the last builder session resumed
            j = Job(prof, a.args[1])
            if a.minutes_set:
                j.c['budget']['worker_minutes'] = a.minutes
                vy.jdump(j.dir / 'job.json', j.c)
            key = f"build-{j.s['attempt']}"
            if key in j.s.get('sids', {}):
                (j.dir / 'logs' / f'{key}.started').touch()
            j.s.pop('escalated', None)
            j.goto('build', reopened=True)
            print(f"{j.id} reopened at build (resuming {key})")
        elif sub in ('run', 'resume'):
            s = Job(prof, a.args[1]).run()
            print(json.dumps({k: s.get(k) for k in ('step', 'cand', 'escalated', 'pr', 'cost_usd', 'attempt')}, default=str))
        elif sub == 'status':
            ids = a.args[1:] or sorted(p.name for p in (Path(prof['home']) / 'jobs').glob('*'))
            for j in ids:
                s = vy.jload(Path(prof['home']) / 'jobs' / j / 'state.json', {})
                print(f"{j}\t{s.get('step', 'new')}\t{s.get('escalated') or (s.get('pr') or {}).get('url', '')}\t${s.get('cost_usd', 0)}")
    elif a.cmd == 'validate':
        wt = vy.worktree(prof, a.cand, Path(prof['home']) / 'wt' / 'validate')
        print(json.dumps(V.validate(prof, wt, vy.git(wt, 'rev-parse', a.base), vy.git(wt, 'rev-parse', a.cand)), indent=1))
    elif a.cmd == 'constraints':
        wt = a.repo or '.'
        if a.args[0] == 'delta':
            d = C.delta(prof, wt, a.args[1], a.args[2])
            print(json.dumps(d, indent=1) if a.json else C.delta_md(d))
        else:
            m = C.cmap(prof, wt, a.args[1:])
            print(json.dumps(m, indent=1) if a.json else C.map_md(m))
    elif a.cmd == 'strengthen':
        import strengthen
        print(json.dumps(strengthen.run(prof, a.args[0], tests=(a.args[1].split(',') if len(a.args) > 1 else None)), indent=1))
    elif a.cmd == 'backtest':
        import backtest
        sys.argv = ['backtest', *a.args, '--profile', prof['name']]
        backtest.main()
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
