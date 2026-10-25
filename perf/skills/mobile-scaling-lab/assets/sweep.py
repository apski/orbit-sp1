#!/usr/bin/env python3
"""Run a config grid under Instruments (Time Profiler + lab signposts), then analyze.

  sweep.py <sweep> [--reps 3] [--udid X] [--tag T]   traces -> traces/<tag>/<sweep>/, resumable
  sweep.py all --arm both --udid <phone>              every sweep (the ~306-run suite) in one go
  sweep.py one -depth 4 -kind tabs ...                single run with explicit app args
  --arm rn|native|both    both = arms interleaved per config, so drift hits both equally
  --tag                   default: "sim" for a simulator UDID, "device" for a physical iPhone

Each run: `xctrace record --launch` starts the app with `-auto 1 <args>`; the autopilot does
startup -> descend -> N pushes -> N pops and exits, which ends the recording.
Reps are interleaved (rep 1 of every config, then rep 2, ...) so drift spreads across configs.
A physical iPhone needs: unlocked, Developer Mode on, connected (cable is steadiest), apps installed with
`UDID=<phone> lab/build.sh` and `... lab/build.sh native`. The apps keep the screen awake during a run.
"""
import itertools, json, os, re, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLES = {'rn': 'com.lab.rnnav', 'native': 'com.lab.nativenav'}
PROCESS = {'com.lab.rnnav': 'NavLab', 'com.lab.nativenav': 'NavLabNative'}

SWEEPS = {
    # nesting depth of navigators above the leaf stack
    'nest': {'depth': [1, 2, 3, 4, 6, 8, 12, 16], 'kind': ['stack', 'tabs', 'mix'], 'pushes': [10]},
    # registered routes per navigator
    'screens': {'screens': [3, 10, 30, 100, 300], 'depth': [2], 'kind': ['stack', 'tabs'], 'pushes': [10]},
    # screen complexity: rows per wireframe (feed cards / form fields / grid rows of 3 tiles)
    'rows': {'rows': [10, 40, 160], 'depth': [1, 4], 'kind': ['mix'], 'pushes': [10]},
    # stack accumulation: the k-th push into one native stack, all previous screens still mounted
    'stack': {'depth': [1], 'pushes': [60], 'freeze': [0, 1]},
    # both at once: does per-push cost scale with depth x registered screens?
    'grid': {'depth': [1, 4, 8], 'screens': [3, 100, 300], 'kind': ['mix'], 'pushes': [10]},
    # the common app shape: 4 tabs -> a stack per tab -> journey stack(s), pushing 40 deep inside a journey
    'app': {'depth': [3, 4], 'kind': ['tabroot'], 'screens': [4], 'pushes': [40]},
    # native-only variant of `rows`: the grid laid out with frames instead of nested UIStackViews (--arm native)
    'gridfix': {'rows': [10, 40, 160], 'depth': [1, 4], 'kind': ['mix'], 'pushes': [10], 'gridlayout': ['frames']},
    # calibrates navprobe's ruler (action -> state settled) against the same configs: RN only, -navprobe 1
    'probecal': {'depth': [1, 4, 8], 'screens': [3, 100, 300], 'kind': ['mix'], 'pushes': [10], 'navprobe': [1]},
    'smoke': {'depth': [3], 'kind': ['mix'], 'pushes': [3]},
}
ALL = ['app', 'nest', 'screens', 'rows', 'stack', 'grid', 'probecal']


def is_device(udid):
    """Physical UDIDs look like 00008130-000144523E62001C; simulators are UUIDs."""
    return bool(re.fullmatch(r'[0-9A-F]{8}-[0-9A-F]{16}', udid))


def device_pids(udid, proc):
    """PIDs of `proc` on a physical iPhone (host pgrep can't see device processes)."""
    with tempfile.NamedTemporaryFile(suffix='.json') as f:
        r = subprocess.run(['xcrun', 'devicectl', 'device', 'info', 'processes', '--device', udid,
                            '--json-output', f.name, '-q'], capture_output=True)
        if r.returncode:
            return []
        procs = json.load(open(f.name)).get('result', {}).get('runningProcesses', [])
    return [p['processIdentifier'] for p in procs if p.get('executable', '').endswith(f'/{proc}.app/{proc}')]


def alive(udid, proc):
    if is_device(udid):
        return bool(device_pids(udid, proc))
    return subprocess.run(['pgrep', '-x', proc], capture_output=True).returncode == 0


def terminate(udid, bundle):
    if is_device(udid):
        for pid in device_pids(udid, PROCESS.get(bundle, bundle.split('.')[-1])):
            subprocess.run(['xcrun', 'devicectl', 'device', 'process', 'terminate', '--device', udid,
                            '--pid', str(pid), '-q'], capture_output=True)
    else:
        subprocess.run(['xcrun', 'simctl', 'terminate', udid, bundle], capture_output=True)


def detach_tools(udid):
    """An attached accessibility client (argent/simwalk ax-service) turns iOS accessibility on, and then
    UITableView builds every row (measured: 40 cells instead of 2) and RN builds AX trees too. The
    argent simulator-server streams the framebuffer. Neither may run while measuring.
    On a phone: keep argent's device runner and VoiceOver off; nothing here can detach them."""
    if is_device(udid):
        return
    # argent <0.30 named the socket /tmp/sd-<udid8>.sock, 0.30+ /tmp/ax-<udid8>.sock; simwalk uses sd-.
    for pat in [f'ax-service --socket /tmp/(sd|ax)-{udid[:8]}', f'simulator-server ios --id {udid}']:
        if subprocess.run(['pkill', '-f', pat]).returncode == 0:
            print(f'  detached: {pat.split()[0]}', flush=True)


LOAD_MAX = float(os.environ.get('LOAD_MAX', '10'))


def wait_for_quiet_host(max_wait=3600):
    """Don't measure on a saturated Mac: other agents' simulators (seen: another sim's mediaanalysisd at
    650% CPU, load 134) swamp the timings. Waits for the 1-minute load average to drop below LOAD_MAX.
    Matters less on a device (its own CPU), but xctrace still streams through the host."""
    t = time.time()
    while os.getloadavg()[0] >= LOAD_MAX and time.time() - t < max_wait:
        if int(time.time() - t) % 60 < 10:
            print(f'  waiting for host: load={os.getloadavg()[0]:.1f} >= {LOAD_MAX}', flush=True)
        time.sleep(10)


def record(out, app_args, udid, bundle, limit=300, tries=3, proc=None, lead=('-auto', '1')):
    """One traced run with hang detection and retries. Logs the host load average.
    Any app: pass proc (its process name) and lead=() (journey.py does). An app without the Lab module never
    exits on its own; xctrace then stops at `limit`, which ends the run normally."""
    poll = 2 if is_device(udid) else 0.5  # devicectl process listing is ~1s
    proc = proc or PROCESS[bundle]
    for attempt in range(1, tries + 1):
        wait_for_quiet_host()
        detach_tools(udid)
        terminate(udid, bundle)
        cmd = ['xcrun', 'xctrace', 'record', '--template', 'Time Profiler', '--device', udid, '--output', out,
               '--time-limit', f'{limit}s', '--no-prompt', '--launch', '--', bundle, *lead, *app_args]
        t, load = time.time(), os.getloadavg()[0]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            # The hang we see is xctrace wedging in its own setup: the hung traces hold no data at all
            # and the app never starts. Catch that in 30s instead of waiting out the full timeout.
            # It can also wedge after the app ran and exited (hung trace again empty), so: app gone and
            # xctrace still alive 45s later is a hang too. A healthy xctrace saves within seconds.
            seen, gone_at = False, None
            while p.poll() is None:
                up = alive(udid, proc)
                seen = seen or up
                if not seen and time.time() - t > 30:
                    raise subprocess.TimeoutExpired(cmd, 30)
                gone_at = (gone_at or time.time()) if seen and not up else None
                if gone_at and time.time() - gone_at > 45:
                    raise subprocess.TimeoutExpired(cmd, 45)
                if time.time() - t > limit + 60:
                    raise subprocess.TimeoutExpired(cmd, limit + 60)
                time.sleep(poll)
            tail = p.stdout.read()[-300:]
            err = '' if os.path.exists(out) else 'FAILED ' + tail
        except subprocess.TimeoutExpired as e:
            stage = {30: 'app never launched', 45: 'app exited, xctrace stuck'}.get(e.timeout, 'run overran')
            # SIGINT makes xctrace stop and save, so the signposts show where the run stalled.
            pat = f'xctrace record .*{out}'
            subprocess.run(['pkill', '-INT', '-f', pat])
            for _ in range(60):
                if subprocess.run(['pgrep', '-f', pat], capture_output=True).returncode:
                    break
                time.sleep(1)
            subprocess.run(['pkill', '-9', '-f', pat])
            terminate(udid, bundle)
            hung = os.path.join(ROOT, 'traces', 'hung', f'{os.path.basename(os.path.dirname(out))}-{time.strftime("%H%M%S")}-{os.path.basename(out)}')
            if os.path.exists(out):
                os.makedirs(os.path.dirname(hung), exist_ok=True)
                shutil.move(out, hung)
            err = f'TIMEOUT: {stage} (stopped, attempt {attempt}/{tries}; trace -> traces/hung/)'
        print(f'  {os.path.basename(out)}  {time.time() - t:.0f}s load={load:.1f} {err}', flush=True)
        if not err and os.getloadavg()[0] >= LOAD_MAX * 1.5:  # host got busy mid-run: measure again
            shutil.rmtree(out, ignore_errors=True)
            err = f'DISCARDED (load rose to {os.getloadavg()[0]:.1f} during the run)'
            print(f'  {os.path.basename(out)}  {err}', flush=True)
            continue
        if not err:
            return True
    return False


def run_sweep(name, arms, reps, udid, tagdir):
    if name == 'probecal':
        arms = ['rn']
    grid = SWEEPS[name]
    configs = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())]
    outdirs = {a: os.path.join(tagdir, name + ('-native' if a == 'native' else '')) for a in arms}
    for d in outdirs.values():
        os.makedirs(d, exist_ok=True)
    print(f'{name}: {len(configs)} configs x {reps} reps x {arms} -> {list(outdirs.values())}', flush=True)
    for rep in range(1, reps + 1):
        for c in configs:
            for a in arms:
                slug = '_'.join(f'{k}{v}' for k, v in c.items())
                out = os.path.join(outdirs[a], f'{slug}_r{rep}.trace')
                if os.path.exists(out):
                    continue
                record(out, [x for k, v in c.items() for x in (f'-{k}', str(v))], udid, BUNDLES[a])
    for d in outdirs.values():
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'analyze.py'), d])


def main():
    a = sys.argv[1:]
    name = a[0]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    reps, udid = int(opt('--reps', '3')), opt('--udid', os.environ.get('UDID', '55BAFC60-D9C9-457C-B068-B12D63A832D8'))
    tagdir = os.path.join(ROOT, 'traces', opt('--tag', 'device' if is_device(udid) else 'sim'))
    arm = opt('--arm', 'rn')
    arms = ['rn', 'native'] if arm == 'both' else [arm]
    if name == 'one':
        flags = {'--arm', '--udid', '--tag', '--reps'}
        app_args = [x for i, x in enumerate(a[1:]) if x not in flags and a[i] not in flags]
        out = os.path.join(tagdir, 'one', time.strftime('%H%M%S') + f'_{arm}' + ''.join(app_args).replace('-', '_') + '.trace')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        record(out, app_args, udid, BUNDLES[arm])
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'analyze.py'), out, '--top', '20'])
        return
    for n in (ALL if name == 'all' else [name]):
        run_sweep(n, arms, reps, udid, tagdir)
    if name == 'all':
        with open(os.path.join(tagdir, 'report.txt'), 'w') as f:
            subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'report.py'), tagdir,
                            '--json', os.path.join(tagdir, 'report.json')], stdout=f, stderr=subprocess.STDOUT)
        print(f'report -> {tagdir}/report.txt', flush=True)
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'model.py'), 'fit', tagdir])  # this target's cost model


if __name__ == '__main__':
    main()
