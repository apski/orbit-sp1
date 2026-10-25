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
BUNDLES = {'rn': 'com.lab.rnnav', 'native': 'com.lab.nativenav',
           # startup lab: the RN app rebuilt with G synthetic JS modules bundled (lab/build_startup.sh)
           'rn-g2k': 'com.lab.rnstartup.g2000', 'rn-g8k': 'com.lab.rnstartup.g8000'}
PROCESS = {'com.lab.rnnav': 'NavLab', 'com.lab.nativenav': 'NavLabNative',
           'com.lab.rnstartup.g2000': 'NavLab', 'com.lab.rnstartup.g8000': 'NavLab'}

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

    # ---- lists lab (-lab lists; docs/labs/lists.md). Keys starting with _ are not knobs:
    # _native overrides grid values for the native arm (or None: RN only), _arms is the default --arm.
    # library x row type, 1000 items: FlatList / FlashList / LegendList vs UICollectionView / UITableView
    'lists': {'lab': ['lists'], 'list': ['flatlist', 'flashlist', 'legend'], 'item': ['simple', 'card', 'heavy', 'mixed'],
              'items': [1000], '_native': {'list': ['collection', 'table']}},
    # item count (data size): virtualized lists should be flat in it
    'listcount': {'lab': ['lists'], 'list': ['flatlist', 'flashlist', 'legend'], 'item': ['card'], 'items': [10, 100, 1000, 10000],
                  '_native': {'list': ['collection']}},
    # no virtualization: ScrollView + map (native arm: the same counts in a collection view)
    'listsv': {'lab': ['lists'], 'list': ['scrollview'], 'item': ['card'], 'items': [10, 100, 300, 1000], '_native': {'list': ['collection']}},
    # a real <Image> (bundled png, decoded once and cached) in place of the media placeholder
    'listimg': {'lab': ['lists'], 'list': ['flatlist', 'flashlist', 'legend'], 'item': ['card'], 'image': [0, 1],
                '_native': {'list': ['collection']}},
    # FlatList windowing: windowSize (screens kept mounted) and initialNumToRender
    'listwin': {'lab': ['lists'], 'list': ['flatlist'], 'item': ['card'], 'window': [3, 5, 11, 21, 51], 'initial': [4, 10], '_native': None},
    # FlashList / LegendList drawDistance (pt rendered ahead)
    'listdraw': {'lab': ['lists'], 'list': ['flashlist', 'legend'], 'item': ['card'], 'draw': [0, 250, 1000, 3000], '_native': None},
    # scroll velocity (pt/s): when does each library fall behind (blank area, hitches)?
    'listspeed': {'lab': ['lists'], 'list': ['flatlist', 'flashlist', 'legend'], 'item': ['mixed'], 'speed': [1500, 3000, 6000, 12000],
                  'distance': [18000], '_native': {'list': ['collection']}},
    # exact item sizes handed to the list (getItemLayout / overrideItemLayout / getFixedItemSize) vs measured
    'listfixed': {'lab': ['lists'], 'list': ['flatlist', 'flashlist', 'legend'], 'item': ['mixed'], 'fixed': [0, 1], '_native': None},
    # A/A: the same config twice per arm; the spread is the measurement floor
    'listaa': {'lab': ['lists'], 'list': ['flatlist'], 'item': ['card'], 'aa': [1, 2], '_native': {'list': ['collection']}},

    # ---- startup lab (-lab startup; docs/labs/startup.md): cold launch -> ready, 5 reps.
    # settle 1500: the app lives 4.5s after ready. xctrace kept nothing at all from a ~3s run.
    # first-screen weight (tile rows x 3), RN vs native
    'startup': {'lab': ['startup'], 'settle': [1500], 'rows': [0, 40, 160], '_reps': 5},
    # eager module execution: of 8000 bundled synthetic modules, run this many before the first render
    'startmods': {'lab': ['startup'], 'settle': [1500], 'modules': [0, 500, 2000, 8000], '_arms': ['rn-g8k'], '_reps': 5},
    # bundle size at fixed execution: 0 / 2000 / 8000 modules bundled, 0 or 2000 executed (rn bundles none)
    'startbundle': {'lab': ['startup'], 'settle': [1500], 'modules': [0, 2000], '_arms': ['rn', 'rn-g2k', 'rn-g8k'], '_reps': 5},
    # native modules: N synthetic Expo modules registered at app start and touched from JS
    'startnatives': {'lab': ['startup'], 'settle': [1500], 'natives': [0, 10, 30, 100], '_arms': ['rn'], '_reps': 5},
    # navigation baseline check after the lists/startup changes: nest depth 3 stack, same args as `nest`
    'navcheck': {'depth': [3], 'kind': ['stack'], 'pushes': [10]},
    # A/A floor for startup
    'startaa': {'lab': ['startup'], 'settle': [1500], 'rows': [40], 'aa': [1, 2], '_reps': 5},
}
LISTS = ['listaa', 'lists', 'listcount', 'listsv', 'listimg', 'listwin', 'listdraw', 'listspeed', 'listfixed']
STARTUP = ['startaa', 'startup', 'startmods', 'startbundle', 'startnatives']
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


# Host gate = busy CPU cores, not the load average. Booted simulators park 100+ runtime daemons as runnable at ~0% CPU,
# so the load average read 500-800 on a host with 8 idle cores and blocked every sweep (gap g0053).
BUSY_MAX = float(os.environ.get('BUSY_MAX', '3'))  # cores; the measured app itself needs ~1-2 while it runs
NCPU = os.cpu_count() or 8


def parse_busy(top_out, ncpu=NCPU):
    """Busy cores from `top -l 2 -n 0` output: the last 'CPU usage' line (the first sample has no delta)."""
    idle = [float(m) for m in re.findall(r'([\d.]+)% idle', top_out)]
    return round((100 - idle[-1]) / 100 * ncpu, 1) if idle else 0.0


def busy_cores():
    return parse_busy(subprocess.run(['top', '-l', '2', '-s', '1', '-n', '0'], capture_output=True, text=True).stdout)


def swapouts():
    """Cumulative swapouts (vm_stat). Rising between two reads = the host is paging: timings and xctrace both suffer."""
    m = re.search(r'Swapouts:\s+(\d+)', subprocess.run(['vm_stat'], capture_output=True, text=True).stdout)
    return int(m.group(1)) if m else 0


def host_busy():
    """(busy cores, swapping?) over ~2s."""
    s0 = swapouts()
    b = busy_cores()
    return b, swapouts() > s0


def wait_for_quiet_host(max_wait=float(os.environ.get('LOAD_WAIT', '3600'))):
    """Don't measure on a saturated Mac: other agents' simulators (seen: another sim's mediaanalysisd at
    650% CPU; another session's bench at 72GB RSS) swamp the timings. Waits until fewer than BUSY_MAX cores
    are busy and nothing is being swapped out.
    Matters less on a device (its own CPU), but xctrace still streams through the host."""
    t = time.time()
    while ((b := host_busy())[0] >= BUSY_MAX or b[1]) and time.time() - t < max_wait:
        if int(time.time() - t) % 60 < 12:
            print(f'  waiting for host: {b[0]} busy cores (max {BUSY_MAX}){", swapping" if b[1] else ""}', flush=True)
        time.sleep(10)


def record(*args, **kw):
    """One traced run at a time on this host: concurrent recordings (sweeps, journeys, other agents) skew each
    other. Every NavLab recorder takes this lock (lab/android too)."""
    import fcntl
    with open('/tmp/navlab-record.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _record(*args, **kw)


def _record(out, app_args, udid, bundle, limit=300, tries=3, proc=None, lead=('-auto', '1')):
    """One traced run with hang detection and retries. Logs busy host cores.
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
        t, load = time.time(), busy_cores()
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
                # A busy host slows xctrace's save: at load 100+ (~8 busy cores) a healthy save took over 45s.
                if gone_at and time.time() - gone_at > 45 * max(1, load / 4):
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
        print(f'  {os.path.basename(out)}  {time.time() - t:.0f}s busy={load:.1f} {err}', flush=True)
        if not err and (b := busy_cores()) >= BUSY_MAX * 1.5:  # host got busy mid-run (app has exited): measure again
            shutil.rmtree(out, ignore_errors=True)
            err = f'DISCARDED ({b} busy cores after the run)'
            print(f'  {os.path.basename(out)}  {err}', flush=True)
            continue
        if not err:
            return True
    return False


def arm_dir(name, arm):
    """rn -> <sweep>, native -> <sweep>-native, rn-g8k -> <sweep>-g8k."""
    return name + {'rn': '', 'native': '-native'}.get(arm, '-' + arm.removeprefix('rn-'))


def arm_configs(grid, arm):
    """Config dicts for one arm. `_native` overrides the native arm's knob values (None: RN only)."""
    if arm == 'native' and '_native' in grid:
        if grid['_native'] is None:
            return []
        grid = {**grid, **grid['_native']}
    knobs = {k: v for k, v in grid.items() if not k.startswith('_')}
    return [dict(zip(knobs, vals)) for vals in itertools.product(*knobs.values())]


def run_sweep(name, arms, reps, udid, tagdir):
    if name == 'probecal':
        arms = ['rn']
    grid = SWEEPS[name]
    per_arm = {a: arm_configs(grid, a) for a in arms}
    per_arm = {a: c for a, c in per_arm.items() if c}
    outdirs = {a: os.path.join(tagdir, arm_dir(name, a)) for a in per_arm}
    for d in outdirs.values():
        os.makedirs(d, exist_ok=True)
    print(f'{name}: {", ".join(f"{a} {len(c)}" for a, c in per_arm.items())} configs x {reps} reps -> {list(outdirs.values())}', flush=True)
    n = max(len(c) for c in per_arm.values())
    for rep in range(1, reps + 1):
        for i in range(n):  # arms interleaved config by config
            for a, configs in per_arm.items():
                if i >= len(configs):
                    continue
                c = configs[i]
                slug = '_'.join(f'{k}{v}' for k, v in c.items())
                out = os.path.join(outdirs[a], f'{slug}_r{rep}.trace')
                if os.path.exists(out):
                    continue
                record(out, [x for k, v in c.items() for x in (f'-{k}', str(v))], udid, BUNDLES[a])
    for d in outdirs.values():
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'analyze.py'), d])


def main():
    a = sys.argv[1:]
    if a[:1] == ['--selftest']:  # pins g0053: the gate reads CPU idle, not the load average
        out = 'CPU usage: 30.0% user, 20.0% sys, 50.00% idle \nCPU usage: 10.0% user, 2.5% sys, 87.50% idle \n'
        assert parse_busy(out, 16) == 2.0, parse_busy(out, 16)
        assert parse_busy('', 16) == 0.0
        assert isinstance(swapouts(), int) and swapouts() > 0, 'vm_stat Swapouts parsed'
        # pins g0039: per-arm knob values (_native), RN-only sweeps, build-variant arms and their dirs
        assert [c['list'] for c in arm_configs(SWEEPS['lists'], 'native')][:2] == ['collection', 'collection']
        assert len(arm_configs(SWEEPS['lists'], 'rn')) == 12 and arm_configs(SWEEPS['listwin'], 'native') == []
        assert [arm_dir('startbundle', a) for a in SWEEPS['startbundle']['_arms']] == ['startbundle', 'startbundle-g2k', 'startbundle-g8k']
        assert all(SWEEPS[n]['settle'] == [1500] for n in STARTUP), 'g0056: xctrace keeps nothing from a ~3s run'
        assert arm_dir('nest', 'native') == 'nest-native' and all(a in BUNDLES for s in SWEEPS.values() for a in s.get('_arms', []))
        print('selftest ok', f'(now: {busy_cores()} busy cores, load avg {os.getloadavg()[0]:.0f})')
        return
    name = a[0]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    reps, udid = int(opt('--reps', '3')), opt('--udid', os.environ.get('UDID', '55BAFC60-D9C9-457C-B068-B12D63A832D8'))
    tagdir = os.path.join(ROOT, 'traces', opt('--tag', 'device' if is_device(udid) else 'sim'))
    arm = opt('--arm', 'rn')
    arms = ['rn', 'native'] if arm == 'both' else arm.split(',')  # or a list: --arm rn,rn-g8k,native
    if name == 'one':
        flags = {'--arm', '--udid', '--tag', '--reps'}
        app_args = [x for i, x in enumerate(a[1:]) if x not in flags and a[i] not in flags]
        out = os.path.join(tagdir, 'one', time.strftime('%H%M%S') + f'_{arm}' + ''.join(app_args).replace('-', '_') + '.trace')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        record(out, app_args, udid, BUNDLES[arm])
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'analyze.py'), out, '--top', '20'])
        return
    group = {'all': ALL, 'lists': LISTS, 'startup-all': STARTUP}
    for n in group.get(name, [name]):
        g = SWEEPS[n]
        # a sweep's own _arms (build variants) always win over --arm
        run_sweep(n, g.get('_arms', arms), int(opt('--reps', g.get('_reps', reps))), udid, tagdir)
    if name == 'all':
        with open(os.path.join(tagdir, 'report.txt'), 'w') as f:
            subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'report.py'), tagdir,
                            '--json', os.path.join(tagdir, 'report.json')], stdout=f, stderr=subprocess.STDOUT)
        print(f'report -> {tagdir}/report.txt', flush=True)
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'model.py'), 'fit', tagdir])  # this target's cost model


if __name__ == '__main__':
    main()
