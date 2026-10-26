"""Shared plumbing for Voyager-1: profiles, git worktrees, the pytest runner, claude sessions. Stdlib only."""
import json, os, re, shutil, subprocess, sys, time, tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

HOME = Path(__file__).resolve().parent
STATE = Path(os.environ.get('VOYAGER_STATE', Path.home() / '.voyager'))


def sh(cmd, cwd=None, timeout=None, env=None, check=False):
    """Run a command, never raise on timeout: returns (rc, stdout+stderr). rc 124 = timeout.
    A shell string (a build) gets its own process group and a timeout kills the whole group: otherwise only `sh` dies
    and xcodebuild keeps running (found reading simslim's simctl.go:370-409). Argument lists (agent sessions) stay in
    the caller's group, so killing a job's controller still takes its builder with it: a resumed job must never race
    an orphaned one."""
    import signal
    group = isinstance(cmd, str)
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                         shell=group, start_new_session=group)
    try:
        so, se = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL) if group else p.kill()
        except ProcessLookupError:
            pass
        so, se = p.communicate()
        return 124, (so or '') + (se or '') + '\n[timeout]'
    out = (so or '') + (se or '')
    if check and p.returncode:
        raise RuntimeError(f'{cmd} -> {p.returncode}\n{out[-2000:]}')
    return p.returncode, out


def git(repo, *args, check=True, timeout=600):
    rc, out = sh(['git', '-C', str(repo), *args], timeout=timeout)
    if check and rc:
        raise RuntimeError(f'git {" ".join(args)} -> {rc}\n{out[-2000:]}')
    return out.strip()


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def profile(spec):
    """An adapter: `<repo>/.voyager/profile.toml` (spec = repo path) or a starter in examples/ (spec = name),
    merged with machine-local `~/.voyager/<name>/local.toml`. Repo-relative paths are resolved against the source."""
    repo = Path(os.path.expanduser(str(spec)))
    f = repo / '.voyager' / 'profile.toml'
    named = next((d / f'{spec}.toml' for d in (STATE / 'profiles', HOME / 'examples') if (d / f'{spec}.toml').exists()), HOME / 'examples' / f'{spec}.toml')
    p = tomllib.loads(f.read_text()) if f.exists() else tomllib.loads(named.read_text())   # private adapters: ~/.voyager/profiles/
    p.setdefault('name', repo.name if f.exists() else str(spec))
    local = STATE / p['name'] / 'local.toml'
    if local.exists():
        p = _merge(p, tomllib.loads(local.read_text()))
    p['source'] = os.path.expanduser(p.get('source') or str(repo))
    p['home'] = str(STATE / p['name'])
    p['bare'] = os.path.expanduser(p.get('bare') or str(STATE / p['name'] / 'repo.git'))
    if p.get('python'):
        py = os.path.expanduser(p['python'])
        p['python'] = py if os.path.isabs(py) else str(Path(p['source']) / py)
    return p


def bare(prof):
    """The isolated object store jobs work from: a local bare clone, refreshed from the source repo.
    The source working tree is never touched (it is usually someone's live work)."""
    b = Path(prof['bare'])
    if not b.exists():
        b.parent.mkdir(parents=True, exist_ok=True)
        sh(['git', 'clone', '--bare', '--local', '-q', prof['source'], str(b)], check=True, timeout=1800)
    return b


def worktree(prof, sha, path, branch=None, sparse=None):
    """Sparse worktree at sha (only prof['sparse'] dirs + root files). Reuses an existing one."""
    path = Path(path)
    b = bare(prof)
    if (path / '.git').exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ['worktree', 'add', '--no-checkout']
    args += ['-B', branch] if branch else ['--detach']
    git(b, *args, str(path), sha)
    # non-cone patterns: root files + the profile's dirs, and never an env file (some repos track .env files,
    # DB certs and APNs keys in git; a worker or a test must not see them)
    pats = ['/*', '!/*/'] + [f'/{d}/' for d in (sparse if sparse is not None else prof.get('sparse', []))] + ['!.env*', '!**/.env*', '!*.pem', '!*.key', '!*.p8', '!*.crt', '!**/certs/']
    git(path, 'sparse-checkout', 'set', '--no-cone', *pats)
    git(path, 'checkout', '-q', *(['-f'] if not branch else []), *([] if branch else [sha]))
    return path


def drop_worktree(prof, path):
    sh(['git', '-C', str(bare(prof)), 'worktree', 'remove', '--force', str(path)])
    shutil.rmtree(path, ignore_errors=True)


def clean_env():
    """No secrets in test or worker processes: only what a shell needs."""
    keep = ('PATH', 'HOME', 'USER', 'LANG', 'TMPDIR', 'SHELL', 'TERM')
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PGCONNECT_TIMEOUT'] = '3'
    return env


def pytest(prof, wt, tests, timeout=600, junit=None):
    """Run test files/node ids in a worktree. Returns {test_id: 'pass'|'fail'|'error'|'skip'}, rc, tail of output.
    A file that fails to collect yields one '<file>::<collection>' error entry."""
    cwd = Path(wt) / prof.get('test_cwd', '.')
    rel = [t[len(prof['test_cwd']) + 1:] if prof.get('test_cwd') and t.startswith(prof['test_cwd'] + '/') else t for t in tests]
    junit = junit or (cwd / f'.vy-junit-{os.getpid()}-{time.time_ns()}.xml')
    cmd = [prof['python'], '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-o', 'junit_family=xunit1',
           f'--junitxml={junit}', *prof.get('pytest_args', []), *rel]
    rc, out = sh(cmd, cwd=cwd, timeout=timeout, env={**clean_env(), **prof.get('test_env', {})})
    res = {}
    try:
        for tc in ET.parse(junit).getroot().iter('testcase'):
            tid = f"{tc.get('classname')}::{tc.get('name')}"
            kids = {c.tag for c in tc}
            res[tid] = 'error' if 'error' in kids else 'fail' if 'failure' in kids else 'skip' if 'skipped' in kids else 'pass'
    except (ET.ParseError, FileNotFoundError):
        pass
    finally:
        Path(junit).unlink(missing_ok=True)
    return res, rc, out[-4000:]


def is_test(prof, path):
    return any(re.search(p, path) for p in prof['test_patterns'])


class InfraError(RuntimeError):
    """The session never reached the model (network, DNS, API outage). Not an agent result: never score it."""


NET = re.compile(r'ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|getaddrinfo|fetch failed|Overloaded|overloaded_error|rate_limit_error')


def infra_error(envelope, raw=''):
    """Only an explicit API failure or a network signature counts: a crash or a timeout is the agent's result."""
    r = str(envelope.get('result') or '')
    if envelope.get('terminal_reason') == 'api_error' or r.startswith('API Error'):
        return True
    return not envelope.get('session_id') and bool(NET.search(raw or ''))


def wait_for_api(max_minutes=60):
    """Block until the API answers (any HTTP status), with backoff. False if it never came back."""
    import urllib.request, urllib.error
    t0, delay = time.time(), 15
    while time.time() - t0 < max_minutes * 60:
        try:
            urllib.request.urlopen('https://api.anthropic.com/', timeout=10)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(delay)
            delay = min(delay * 2, 300)
    return False


def claude(prompt, cwd, log, model='opus', max_turns=80, timeout=1800, allowed=None, disallowed=None,
           schema=None, resume=None, mcp=None, budget=None, session_id=None, env_extra=None, _retry_left=3):
    """One headless Claude Code session. Returns the parsed --output-format json envelope (+ rc, secs).
    Workers get no MCP servers unless `mcp` names a config file (the recorder gets argent only)."""
    cmd = ['claude', '-p', prompt, '--output-format', 'json', '--model', model, '--max-turns', str(max_turns)]
    cmd += ['--strict-mcp-config'] + (['--mcp-config', str(mcp)] if mcp else [])
    if allowed:
        cmd += ['--permission-mode', 'dontAsk', '--allowedTools', *allowed]
    else:
        cmd += ['--permission-mode', 'bypassPermissions']
    if disallowed:
        cmd += ['--disallowedTools', *disallowed]
    if schema:
        cmd += ['--json-schema', json.dumps(schema)]
    if resume:
        cmd += ['--resume', resume]
    elif session_id:
        cmd += ['--session-id', session_id]
    if budget:
        cmd += ['--max-budget-usd', str(budget)]
    env = {**clean_env(), **(env_extra or {})}
    if os.environ.get('VOYAGER_FAKE_CLAUDE'):
        # selftest: a scripted stand-in gets the prompt on stdin and answers with the same JSON envelope
        env.update({k: v for k, v in os.environ.items() if k.startswith('VOYAGER_')})
        cmd = [sys.executable, os.environ['VOYAGER_FAKE_CLAUDE'], *(['--resume'] if resume else [])]
        try:
            p = subprocess.run(cmd, cwd=cwd, input=prompt, capture_output=True, text=True, env=env, timeout=timeout)
            rc, out = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            rc, out = 124, ''
        cmd = None
    t0 = time.time()
    if cmd:
        rc, out = sh(cmd, cwd=cwd, timeout=timeout, env=env)
    secs = round(time.time() - t0)
    Path(log).write_text(out)
    env_ = {}
    try:
        env_ = json.loads(out[out.index('{'):]) if '{' in out else {}
    except json.JSONDecodeError:
        # stderr noise before/after the JSON line
        for line in out.splitlines():
            if line.startswith('{') and '"type"' in line:
                try:
                    env_ = json.loads(line)
                except json.JSONDecodeError:
                    pass
    env_.update(rc=rc, secs=secs)
    if infra_error(env_, out):
        Path(str(log) + '.infra').write_text(str(env_.get('result'))[:500])
        if _retry_left > 0 and not os.environ.get('VOYAGER_FAKE_CLAUDE') and wait_for_api():
            # same session id may exist with zero turns; resume it if so, else start clean
            return claude(prompt, cwd, log, model, max_turns, timeout, allowed, disallowed, schema,
                          resume or (env_.get('session_id') if env_.get('num_turns') else None), mcp, budget,
                          None if env_.get('num_turns') else session_id, env_extra, _retry_left - 1)
        raise InfraError(str(env_.get('result'))[:300])
    return env_


# Bash patterns no worker may run: anything that leaves the machine or touches prod.
WORKER_DENY = ['Bash(git push*)', 'Bash(*ssh *)', 'Bash(*psql*)', 'Bash(*rw.sh*)', 'Bash(*docker*)',
               'Bash(*send-email*)', 'Bash(*curl*)', 'Bash(*gh *)', 'WebFetch', 'WebSearch']


def jdump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + '\n')
    tmp.replace(path)


def jload(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def log(*a):
    print(*a, file=sys.stderr, flush=True)
