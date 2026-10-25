#!/usr/bin/env python3
"""Android sweeps: the iOS config grids (lab/sweep.py SWEEPS) under Perfetto on an emulator or device.

  lab/android/sweep.py <sweep> [--reps 3] [--serial emulator-5556] [--tag android-emu] [--arm rn|native|both]
  lab/android/sweep.py all --arm both                every sweep, then report + model fit
  lab/android/sweep.py one -depth 4 -kind tabs ...   single traced run + analysis printout
  lab/android/sweep.py aa --arm both --reps 6         A/A floor: one config repeated, spread per arm

Each run: start Perfetto (lab/android/navlab.pbtx), `am start` the app with `--es args "-auto 1 <args>"`; the
autopilot does startup -> descend -> N pushes -> N pops and exits the process; then Perfetto is stopped and
the trace pulled to traces/<tag>/<sweep>[-native]/<slug>_r<rep>.pftrace. analyze.py writes the same
runs.csv / intervals.csv columns as the iOS pipeline, so lab/report.py and lab/model.py run unchanged.
Install first: lab/android/build.sh (both arms, Release).
"""
import itertools, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
from sweep import SWEEPS, ALL, BUSY_MAX, host_busy, wait_for_quiet_host  # noqa: E402  the iOS grids and host gate

SWEEPS = {**SWEEPS, 'aa': {'depth': [3], 'kind': ['mix'], 'pushes': [10]}}  # A/A noise floor (run with --reps 6)
APPS = {'rn': ('com.lab.rnnav', 'com.lab.rnnav/.MainActivity'),
        'native': ('com.lab.nativenav', 'com.lab.nativenav/lab.nativenav.MainActivity')}
CFG_DEV = '/data/misc/perfetto-configs/navlab.pbtx'


def adb(serial, *a, check=False, **kw):
    return subprocess.run(['adb', '-s', serial, *a], capture_output=True, text=True, check=check, **kw)


def sh(serial, cmd, **kw):
    return adb(serial, 'shell', cmd, **kw)


def pid(serial, pkg):
    return sh(serial, f'pidof {pkg}').stdout.strip()


GUEST_LOAD_MAX = float(os.environ.get('GUEST_LOAD_MAX', '6'))


def guest_load(serial):
    try:
        return float(sh(serial, 'cat /proc/loadavg').stdout.split()[0])
    except (IndexError, ValueError):
        return 99.0


def wait_for_quiet_guest(serial, max_wait=900):
    """The emulator's own 1-min load: post-boot dexopt, GMS jobs and a starved host all show up here. On a host
    shared with other agents' simulators the host load average is a poor signal (seen: 600-1000 with half the
    CPU idle, hundreds of runnable sim daemons), so the guest is gated too, and A/A floors say how much noise is left."""
    t = time.time()
    while guest_load(serial) >= GUEST_LOAD_MAX and time.time() - t < max_wait:
        time.sleep(5)
    return guest_load(serial)


def prepare(serial):
    """Screen on and awake, no animations scaled, no accessibility client attached."""
    adb(serial, 'push', os.path.join(HERE, 'navlab.pbtx'), CFG_DEV, check=True)
    for c in ['svc power stayon true', 'settings put system screen_off_timeout 1800000', 'input keyevent KEYCODE_WAKEUP',
              'wm dismiss-keyguard', 'settings put global hide_error_dialogs 1']:  # no ANR dialog over the app
        sh(serial, c)


def detach_tools(serial):
    """An attached UiAutomation client (argent's / uiautomator dump) turns accessibility on for the app,
    which makes RN and Compose build accessibility trees: the Android twin of the iOS ax-service rule."""
    # argent's on-device helper holds a UiAutomation connection (accessibility_enabled=1) while it lives.
    if pid(serial, 'com.argent.androiddevtools'):
        sh(serial, 'am force-stop com.argent.androiddevtools')
        print('  detached: com.argent.androiddevtools', flush=True)
    if sh(serial, 'pkill -f uiautomator').returncode == 0:
        print('  detached: uiautomator', flush=True)
    for _ in range(10):  # the system drops the flag shortly after the last client goes
        if sh(serial, 'settings get secure accessibility_enabled').stdout.strip() in ('0', 'null'):
            return
        time.sleep(0.5)
    print('  WARNING accessibility still enabled: '
          + sh(serial, 'settings get secure enabled_accessibility_services').stdout.strip(), flush=True)


def record(*args, **kw):
    """One traced run at a time on this host, shared with lab/sweep.py's recorders (same lock file)."""
    import fcntl
    with open('/tmp/navlab-record.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _record(*args, **kw)


def _record(out, app_args, serial, arm, limit=300, tries=3):
    pkg, comp = APPS[arm]
    dev = f'/data/misc/perfetto-traces/navlab-{os.getpid()}.pftrace'
    for attempt in range(1, tries + 1):
        wait_for_quiet_host()
        detach_tools(serial)
        sh(serial, f'am force-stop {pkg}')
        sh(serial, f'rm -f {dev}')
        gload = wait_for_quiet_guest(serial)
        t, load = time.time(), host_busy()[0]
        p = sh(serial, f'perfetto --background --txt -c {CFG_DEV} -o {dev}')
        ppid = p.stdout.strip().split()[-1] if p.stdout.strip() else ''
        if not ppid.isdigit():
            print(f'  perfetto failed to start: {p.stdout} {p.stderr}', flush=True)
            continue
        time.sleep(1.5)  # tracing session up before the app starts
        args = ' '.join(['-auto', '1', *app_args])
        sh(serial, f"am start -S -n {comp} --es args '{args}'")
        err, seen = '', False
        while True:
            up = bool(pid(serial, pkg))
            seen = seen or up
            if not seen and time.time() - t > 30:
                err = 'TIMEOUT: app never launched'
                break
            if seen and not up:
                break
            if time.time() - t > limit:
                err = 'TIMEOUT: run overran'
                break
            time.sleep(0.5)
        sh(serial, f'kill -TERM {ppid}')
        for _ in range(60):  # perfetto flushes and writes the file on SIGTERM
            if not sh(serial, f'kill -0 {ppid} 2>/dev/null && echo up').stdout.strip():
                break
            time.sleep(0.5)
        sh(serial, f'am force-stop {pkg}')
        last = -1
        for _ in range(40):  # traced finishes writing after the perfetto client exits: wait for a stable size
            size = sh(serial, f'stat -c %s {dev} 2>/dev/null').stdout.strip()
            if size.isdigit() and int(size) > 0 and int(size) == last:
                break
            last = int(size) if size.isdigit() else -1
            time.sleep(0.5)
        if not err:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            r = adb(serial, 'pull', dev, out)
            err = '' if r.returncode == 0 and os.path.getsize(out) > 0 else f'FAILED pull: {r.stderr.strip()}'
        sh(serial, f'rm -f {dev}')
        print(f'  {os.path.basename(out)}  {time.time() - t:.0f}s busy={load} guest={gload:.1f} {err}', flush=True)
        if not err and (b := host_busy())[0] >= BUSY_MAX * 1.5:  # host got busy mid-run: measure again
            os.remove(out)
            print(f'  {os.path.basename(out)}  DISCARDED ({b[0]} busy cores at the end of the run)', flush=True)
            continue
        if not err:
            return True
        if os.path.exists(out):
            os.remove(out)
    return False


def analyze(path, *extra):
    subprocess.run([sys.executable, os.path.join(HERE, 'analyze.py'), path, *extra])


def run_sweep(name, arms, reps, serial, tagdir):
    if name == 'probecal':
        arms = ['rn']
    grid = SWEEPS[name]
    configs = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())]
    outdirs = {a: os.path.join(tagdir, name + ('-native' if a == 'native' else '')) for a in arms}
    print(f'{name}: {len(configs)} configs x {reps} reps x {arms} -> {list(outdirs.values())}', flush=True)
    for rep in range(1, reps + 1):  # interleaved: rep 1 of every config and arm, then rep 2, ...
        for c in configs:
            for a in arms:
                slug = '_'.join(f'{k}{v}' for k, v in c.items())
                out = os.path.join(outdirs[a], f'{slug}_r{rep}.pftrace')
                if not os.path.exists(out):
                    record(out, [x for k, v in c.items() for x in (f'-{k}', str(v))], serial, a)
    for d in outdirs.values():
        analyze(d)


def main():
    a = sys.argv[1:]
    if not a:
        return print(__doc__)
    name = a[0]
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    reps, serial = int(opt('--reps', '3')), opt('--serial', os.environ.get('SERIAL', 'emulator-5556'))
    tagdir = os.path.join(ROOT, 'traces', opt('--tag', 'android-emu'))
    arm = opt('--arm', 'rn')
    arms = ['rn', 'native'] if arm == 'both' else [arm]
    prepare(serial)
    if name == 'one':
        flags = {'--arm', '--serial', '--tag', '--reps'}
        app_args = [x for i, x in enumerate(a[1:]) if x not in flags and a[i] not in flags]
        out = os.path.join(tagdir, 'one', time.strftime('%H%M%S') + f'_{arm}' + ''.join(app_args).replace('-', '_') + '.pftrace')
        if record(out, app_args, serial, arm):
            analyze(out)
        return
    for n in (ALL if name == 'all' else [name]):
        run_sweep(n, arms, reps, serial, tagdir)
    if name == 'all':
        with open(os.path.join(tagdir, 'report.txt'), 'w') as f:
            subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'report.py'), tagdir,
                            '--json', os.path.join(tagdir, 'report.json')], stdout=f, stderr=subprocess.STDOUT)
        subprocess.run([sys.executable, os.path.join(ROOT, 'lab', 'model.py'), 'fit', tagdir])


if __name__ == '__main__':
    main()
