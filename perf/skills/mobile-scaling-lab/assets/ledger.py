#!/usr/bin/env python3
"""ledger: every architecture move is a prediction; measure it afterwards and score how calibrated we are.

  ledger.py add --feature F --move design-afresh --metric mods_per_commit --baseline 1.33 --predicted 1.13 --lo 1.05 --hi 1.25
                --measure "<shell command whose last output line is the metric>" [--note "..."] [--ledger .navlab/ledger.jsonl]
  ledger.py measure <id>      run the entry's measure command, record the actual value, close it
  ledger.py score             calibration: hit rate (actual inside [lo, hi]), direction right, mean error, bias, per move

The outside judge for the steward and migration skills: a move that "worked" but missed its prediction teaches the
next estimate; a prediction interval that's always hit is too wide, one that's rarely hit is overconfident.
"""
import json, os, re, statistics as st, subprocess, sys, time


def load(path):
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def save(path, rows):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        f.writelines(json.dumps(r) + '\n' for r in rows)


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    path = opt('--ledger', '.navlab/ledger.jsonl')
    rows = load(path)
    cmd = a[0] if a else 'score'
    if cmd == 'add':
        e = {'id': f"{time.strftime('%Y%m%d')}-{re.sub(r'[^a-z0-9]+', '-', opt('--feature', 'x').lower())[:30]}-{len(rows) + 1}",
             'date': time.strftime('%Y-%m-%d'), 'feature': opt('--feature'), 'move': opt('--move'), 'metric': opt('--metric'),
             'baseline': float(opt('--baseline')), 'predicted': float(opt('--predicted')), 'lo': float(opt('--lo')), 'hi': float(opt('--hi')),
             'measure': opt('--measure'), 'note': opt('--note', ''), 'status': 'open', 'actual': None}
        rows.append(e)
        save(path, rows)
        print(f"added {e['id']}: {e['metric']} {e['baseline']} -> {e['predicted']} [{e['lo']}, {e['hi']}]")
    elif cmd == 'measure':
        e = next(r for r in rows if r['id'] == a[1])
        out = subprocess.run(e['measure'], shell=True, capture_output=True, text=True).stdout.strip().split('\n')[-1]
        e['actual'] = float(re.findall(r'-?\d+(?:\.\d+)?', out)[-1])
        e['status'], e['measured'] = 'done', time.strftime('%Y-%m-%d')
        save(path, rows)
        print(f"{e['id']}: predicted {e['predicted']} [{e['lo']}, {e['hi']}], actual {e['actual']} -> {'HIT' if e['lo'] <= e['actual'] <= e['hi'] else 'MISS'}")
    elif cmd == 'score':
        done = [r for r in rows if r['status'] == 'done']
        print(f'{len(rows)} predictions, {len(done)} measured, {sum(r["status"] == "open" for r in rows)} open')
        groups = {}
        for r in done:
            groups.setdefault(r['move'], []).append(r)
        for move, rs in sorted(groups.items()):
            hit = sum(r['lo'] <= r['actual'] <= r['hi'] for r in rs) / len(rs)
            direction = sum((r['actual'] - r['baseline']) * (r['predicted'] - r['baseline']) > 0 for r in rs) / len(rs)
            rel = [abs(r['actual'] - r['predicted']) / abs(r['predicted']) for r in rs if r['predicted']]
            bias = st.mean(r['predicted'] - r['actual'] for r in rs)
            verdict = 'overconfident' if hit < 0.5 else 'too cautious' if hit > 0.95 and len(rs) >= 5 else 'calibrated'
            print(f'  {move:<16} n={len(rs):<3} interval hit {hit:.0%}  direction right {direction:.0%}  '
                  f'mean rel error {st.mean(rel):.0%}  bias {bias:+.2f}  -> {verdict}')
        for r in [r for r in rows if r['status'] == 'open']:
            print(f"  open: {r['id']} ({r['metric']} {r['baseline']} -> {r['predicted']}); run: ledger.py measure {r['id']}")


if __name__ == '__main__':
    main()
