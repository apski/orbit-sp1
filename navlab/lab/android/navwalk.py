#!/usr/bin/env python3
"""LLM-free regression walk for both Android arms (the Android counterpart of flows/nav-walk.mjs; simwalk is iOS-only).

  lab/android/navwalk.py [--arm rn|native|both] [--repeat 3] [--serial emulator-5556]

Depth-3 mixed tree (tabs > stack > leaf stack): descend, push 3, pop 1 (system back), switch tabs away and back
(the leaf stack must survive), pop to Home. Every navigating tap has an `until` that only the destination has;
taps are re-sent if the destination doesn't appear. Selectors come from a fresh `uiautomator dump` (argent's
cached tree goes stale after Navigation-Compose transitions). Prints a step table; exit 1 on any failure.
"""
import re, subprocess, sys, time

APPS = {'rn': ('com.lab.rnnav', 'com.lab.rnnav/.MainActivity'),
        'native': ('com.lab.nativenav', 'com.lab.nativenav/lab.nativenav.MainActivity')}


class D:
    def __init__(self, serial):
        self.s = serial

    def sh(self, cmd):
        return subprocess.run(['adb', '-s', self.s, 'shell', cmd], capture_output=True, text=True).stdout

    def nodes(self):
        xml = self.sh('uiautomator dump /data/local/tmp/navwalk.xml >/dev/null && cat /data/local/tmp/navwalk.xml')
        out = []
        for n in re.finditer(r'<node [^>]*>', xml):
            a = dict(re.findall(r'([\w-]+)="([^"]*)"', n[0]))
            b = [int(v) for v in re.findall(r'\d+', a.get('bounds', ''))]
            if len(b) == 4 and b[2] > b[0] and b[3] > b[1]:
                out.append((a.get('resource-id', ''), a.get('text', '') or a.get('content-desc', ''), b))
        return out

    def find(self, sel):
        """sel: {'id': x} (exact resource-id) or {'label': x} (exact text / content-desc)."""
        for rid, label, b in self.nodes():
            if ('id' in sel and rid == sel['id']) or ('label' in sel and label == sel['label']):
                return b
        return None

    def wait(self, sel, ms=30000, present=True):
        t = time.time()
        while time.time() - t < ms / 1000:
            if bool(self.find(sel)) == present:
                return
        raise AssertionError(f'{"never appeared" if present else "still present"}: {sel}')

    def tap(self, sel, until, tries=2):
        for _ in range(tries):
            b = self.find(sel)
            if not b:
                raise AssertionError(f'not found: {sel}')
            self.sh(f'input tap {(b[0] + b[2]) // 2} {(b[1] + b[3]) // 2}')
            try:
                # generous: a dump takes seconds on a busy emulator, and an early re-tap pushes twice
                return self.wait(until, 20000)
            except AssertionError:
                pass  # swallowed touch: re-tap
        raise AssertionError(f'tap {sel} never reached {until}')

    def back(self, until, gone):
        self.sh('input keyevent KEYCODE_BACK')
        self.wait(until)
        self.wait(gone, present=False)


def walk(d, arm):
    pkg, comp = APPS[arm]
    steps = []

    def step(name, fn):
        t = time.time()
        fn()
        steps.append((name, time.time() - t))

    def launch():
        d.sh(f"am start -S -n {comp} --es args '-depth 3 -kind mix'")
        d.wait({'id': 'hub.next.1'}, 60000)

    step('launch depth 3 mix', launch)
    step('descend to stack L2', lambda: d.tap({'id': 'hub.next.1'}, {'id': 'hub.next.2'}))
    step('descend to leaf L3', lambda: d.tap({'id': 'hub.next.2'}, {'id': 'home.push'}))
    step('push #1', lambda: d.tap({'id': 'home.push'}, {'label': 'Detail #1 · d3'}))
    step('push #2', lambda: d.tap({'id': 'detail.push'}, {'label': 'Detail #2 · d3'}))
    step('push #3', lambda: d.tap({'id': 'detail.push'}, {'label': 'Detail #3 · d3'}))
    step('pop to #2', lambda: d.back({'label': 'Detail #2 · d3'}, {'label': 'Detail #3 · d3'}))
    step('switch to Hub1 tab', lambda: d.tap({'label': 'Hub1'}, {'label': 'Hub 1/3'}))
    step('back to L2 tab keeps #2', lambda: d.tap({'label': 'L2'}, {'label': 'Detail #2 · d3'}))
    step('pop to #1', lambda: d.back({'label': 'Detail #1 · d3'}, {'label': 'Detail #2 · d3'}))
    step('pop to Home', lambda: d.back({'id': 'home.push'}, {'label': 'Detail #1 · d3'}))
    d.sh(f'am force-stop {pkg}')
    return steps


def main():
    a = sys.argv[1:]
    opt = lambda k, v: a[a.index(k) + 1] if k in a else v
    d = D(opt('--serial', 'emulator-5556'))
    arms = ['rn', 'native'] if opt('--arm', 'both') == 'both' else [opt('--arm', 'both')]
    ok = True
    for arm in arms:
        for rep in range(1, int(opt('--repeat', '1')) + 1):
            try:
                steps = walk(d, arm)
                print(f'{arm} run {rep}: PASS  ' + '  '.join(f'{n} {s:.1f}s' for n, s in steps))
            except AssertionError as e:
                ok = False
                print(f'{arm} run {rep}: FAIL  {e}\n  screen: {[(r, l) for r, l, _ in d.nodes() if r or l][:25]}')
    d.sh('pkill -f uiautomator')  # leave no accessibility client attached (measurement hygiene)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
