#!/usr/bin/env python3
"""Snapshot a target's results into the pack (small CSV/JSON only; raw .trace files stay local).

  baseline.py traces/sim sim-iphone17pro-ios26.4 [--device "iPhone 17 Pro sim" --os 26.4]
"""
import json, os, platform, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src, name = sys.argv[1], sys.argv[2]
opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
dst = os.path.join(ROOT, 'baseline', name)
os.makedirs(dst, exist_ok=True)
for f in ('model.json', 'report.txt', 'report.json'):
    if os.path.exists(os.path.join(src, f)):
        shutil.copy(os.path.join(src, f), dst)
sweeps = []
for d in sorted(os.listdir(src)):
    if os.path.exists(os.path.join(src, d, 'runs.csv')):
        os.makedirs(os.path.join(dst, d), exist_ok=True)
        for f in ('runs.csv', 'intervals.csv'):
            shutil.copy(os.path.join(src, d, f), os.path.join(dst, d, f))
        sweeps.append(d)
pkg = json.load(open(os.path.join(ROOT, 'app', 'package.json')))
meta = {'name': name, 'source': os.path.relpath(src, ROOT), 'snapshot': time.strftime('%Y-%m-%d'), 'device': opt('--device', ''), 'os': opt('--os', ''),
        'react-native': pkg['dependencies']['react-native'], 'react-navigation': pkg['dependencies']['@react-navigation/native'],
        'expo': pkg['dependencies']['expo'], 'xcode': subprocess.run(['xcodebuild', '-version'], capture_output=True, text=True).stdout.split('\n')[0],
        'host': platform.node(), 'build': 'Release', 'sweeps': sweeps}
json.dump(meta, open(os.path.join(dst, 'meta.json'), 'w'), indent=1)
print(f'baseline -> {dst} ({len(sweeps)} sweeps)')
