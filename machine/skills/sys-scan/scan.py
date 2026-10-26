#!/usr/bin/env python3
"""sys-scan: why is this Mac slow? Load, booted simulators (the usual cause), leaked duplicates, stuck and heavy
processes for the current user. Read-only: kills nothing. One `ps` pass, no `top`/`simctl`, so it stays cheap when the
machine is already overloaded.

  scan.py            report
  scan.py --json     the same as JSON
  scan.py --test     self-check of the parsers
"""
import json, os, plistlib, re, subprocess, sys
from collections import defaultdict

DEV = re.compile(r'\b(node|bun|deno|python[\d.]*|ruby|java|gradle|xcodebuild|simctl|metro|esbuild|vite|webpack|jest|'
                 r'tsserver|qemu-system\S*|orbitfleet|argent|watchman|mcp\S*)\b', re.I)
SYSTEM = re.compile(r'^(/System/|/usr/|/Library/Developer/|/Library/Apple/)|\.(xpc|appex|app)/Contents/')
SIM = re.compile(r'launchd_sim (\S*/Devices/([0-9A-F-]{36}))/')
MEM_HOG, CPU_HOG, STALE_H = 1 << 20, 50.0, 6  # rss KB (1 GB), % of one core, hours


def sh(*a, timeout=15):
    try: return subprocess.run(a, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError): return ''


def etime_h(s):  # ps etime: [[dd-]hh:]mm:ss
    d, _, rest = s.rpartition('-')
    parts = [int(p) for p in rest.split(':')]
    while len(parts) < 3: parts.insert(0, 0)
    return int(d or 0) * 24 + parts[0] + parts[1] / 60


def parse_ps(out):
    procs = {}
    for line in out.splitlines():
        f = line.split(None, 8)
        if len(f) == 9:
            procs[int(f[0])] = dict(pid=int(f[0]), ppid=int(f[1]), uid=int(f[2]), stat=f[3], age=etime_h(f[4]),
                                    cpu=float(f[5]), rss=int(f[6]), tty=f[7], cmd=f[8])
    return procs


def subtree(procs, root):
    kids = defaultdict(list)
    for v in procs.values(): kids[v['ppid']].append(v['pid'])
    out, todo = set(), [root]
    while todo:
        p = todo.pop(); out.add(p); todo += kids[p]
    return out


def scan():
    procs = parse_ps(sh('ps', '-axo', 'pid=,ppid=,uid=,stat=,etime=,%cpu=,rss=,tty=,command=', timeout=90))
    if not procs: sys.exit('sys-scan: ps returned nothing (timed out?); the machine may be too loaded to scan')
    uid, protected, p = os.getuid(), set(), os.getpid()
    while p in procs and p not in protected: protected.add(p); p = procs[p]['ppid']  # this agent, its terminal, tmux

    leases = sh('orbitfleet', 'ls', '--json', timeout=60) or None  # None = unknown (timed out): never read as 'not leased'
    sims, in_sim = [], set()
    for v in procs.values():
        m = SIM.search(v['cmd'])
        if not m: continue
        tree = subtree(procs, v['pid']); in_sim |= tree
        try: info = plistlib.load(open(m[1] + '/device.plist', 'rb'))
        except OSError: info = {}
        sims.append(dict(udid=m[2], name=info.get('name', '?'), runtime=info.get('runtime', '?').rsplit('.', 1)[-1],
                         procs=len(tree), cpu=round(sum(procs[q]['cpu'] for q in tree)), rss_mb=sum(procs[q]['rss'] for q in tree) >> 10,
                         age_h=round(v['age'], 1), leased=None if leases is None else m[2] in leases))
    sims.sort(key=lambda s: -s['cpu'])

    managed = {int(l.split()[0]) for l in sh('launchctl', 'list').splitlines()[1:] if l.split()[0].isdigit()}
    mine = [v for v in procs.values() if v['uid'] == uid and v['pid'] not in protected and v['pid'] not in in_sim]

    groups = defaultdict(list)
    for v in mine:
        if v['tty'] == '??' and not SYSTEM.search(v['cmd']): groups[v['cmd']].append(v)
    dups = [dict(count=len(g), pids=sorted(v['pid'] for v in g), cpu=round(sum(v['cpu'] for v in g), 1),
                 rss_mb=sum(v['rss'] for v in g) >> 10, oldest_h=round(max(v['age'] for v in g), 1),
                 orphans=sum(v['ppid'] == 1 for v in g), cmd=cmd[:160]) for cmd, g in groups.items()
            if len(g) > 1 and not cmd.startswith(('(', '<')) and (DEV.search(cmd) or sum(v['rss'] for v in g) >= 50 << 10)]  # skip exiting/zombie stubs and small herds (cat, sleep, postgres workers)
    dups.sort(key=lambda d: (-d['count'], -d['rss_mb']))
    in_dup = {p for d in dups for p in d['pids']}

    flagged = []
    for v in mine:
        why = []
        if 'Z' in v['stat']: why.append(f"zombie: kill parent {v['ppid']}")
        if 'U' in v['stat']: why.append('uninterruptible (unkillable until I/O returns)')
        if 'T' in v['stat']: why.append('stopped (kill, or kill -CONT)')
        if v['rss'] >= MEM_HOG: why.append(f"rss {v['rss'] / (1 << 20):.1f}G")
        if v['cpu'] >= CPU_HOG: why.append(f"cpu {v['cpu']:.0f}%")
        if (v['tty'] == '??' and v['ppid'] == 1 and v['age'] >= STALE_H and DEV.search(v['cmd'])
                and v['pid'] not in managed and v['pid'] not in in_dup):
            why.append(f"orphaned dev process, {v['age']:.0f}h old")
        if why:
            flagged.append(dict(pid=v['pid'], ppid=v['ppid'], cpu=v['cpu'], rss_mb=v['rss'] >> 10, age_h=round(v['age'], 1),
                                managed=v['pid'] in managed, why=why, cmd=v['cmd'][:160]))
    flagged.sort(key=lambda r: (-r['cpu'], -r['rss_mb']))

    return dict(load=[float(x) for x in sh('sysctl', '-n', 'vm.loadavg').strip('{} \n').split()],
                cores=os.cpu_count(), procs=len(procs), sims=sims, duplicates=dups, processes=flagged)


def report(r):
    print(f"load {' '.join(map(str, r['load']))} on {r['cores']} cores; {r['procs']} processes; {len(r['sims'])} booted simulators")
    print(f"\nSIMULATORS{'':6}{'PROCS':>6} {'CPU':>5} {'RSS*':>7} {'AGE':>6}  LEASE   (*RSS sums shared pages: relative, not absolute)")
    for s in r['sims']:
        print(f"{s['udid'][:8]} {s['name'][:22]:22} {s['runtime'][:8]:8} {s['procs']:>5} {s['cpu']:>4}% {s['rss_mb']:>6}M {s['age_h']:>5}h  "
              f"{'?' if s['leased'] is None else 'leased' if s['leased'] else '-'}")
    print(f"\nDUPLICATES{'':4}{'N':>3} {'ORPH':>4} {'CPU':>5} {'RSS':>7} {'OLDEST':>7}  COMMAND / PIDS")
    for d in r['duplicates']:
        print(f"{'':14}{d['count']:>3} {d['orphans']:>4} {d['cpu']:>4}% {d['rss_mb']:>6}M {d['oldest_h']:>6}h  {d['cmd']}\n{'':48}{d['pids']}")
    print(f"\nFLAGGED{'':3}{'PID':>6} {'PPID':>6} {'CPU':>5} {'RSS':>7} {'AGE':>6}  WHY / COMMAND")
    for p in r['processes']:
        print(f"{'':10}{p['pid']:>6} {p['ppid']:>6} {p['cpu']:>4.0f}% {p['rss_mb']:>6}M {p['age_h']:>5}h  "
              f"{'; '.join(p['why'])}{' [launchd]' if p['managed'] else ''}\n{'':48}{p['cmd']}")


def test():
    assert etime_h('09:24') == 0.15 and etime_h('01-01:30:28') == 25.5 and etime_h('05:57:55') == 5 + 57 / 60
    ps = parse_ps('  1 0 0 Ss 11-02:25:41 0.0 100 ?? /sbin/launchd\n'
                  ' 10 1 501 S 01:00:00 1.5 2048 ?? launchd_sim /x/Devices/E916DD1D-A6D0-49AC-B033-04FC906DBF32/data/run.plist\n'
                  ' 11 10 501 R 59:00 90.0 4096 ?? /sim/app with args')
    assert ps[11]['cmd'] == '/sim/app with args' and ps[11]['cpu'] == 90.0 and subtree(ps, 10) == {10, 11}
    assert SIM.search(ps[10]['cmd'])[2] == 'E916DD1D-A6D0-49AC-B033-04FC906DBF32'
    assert SYSTEM.search('/System/Library/x.xpc/Contents/MacOS/x') and not SYSTEM.search('./orbitfleet serve')
    print('ok')


if __name__ == '__main__':
    if '--test' in sys.argv: test()
    else:
        r = scan()
        print(json.dumps(r, indent=1)) if '--json' in sys.argv else report(r)
