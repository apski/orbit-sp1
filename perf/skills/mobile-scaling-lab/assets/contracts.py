#!/usr/bin/env python3
"""contracts: what a feature promises the outside world, and what it relies on. These must survive any redesign or migration.

  contracts.py <facts.json> [--scope depgraph.json] [--json out]     inventory (whole app, or one feature)
  contracts.py diff <before.json> <after.json>                       what disappeared / appeared between two inventories

Kinds: api (REST paths, GraphQL/Relay operations), route (navigation targets), deeplink (linking config paths),
storage (persisted keys), analytics (events), flag (feature flags), native (native modules/views).
With --scope, each contract also lists users OUTSIDE the feature: a storage key or event the rest of the app reads is a
shared contract; changing it ripples beyond the feature. Every entry carries file:line.
"""
import json, sys
from collections import defaultdict

KINDS = {
    'api': lambda x: f"{x.get('kind')}:{x.get('method') or x.get('op') or ''} {x.get('target')}",
    'nav': lambda x: x.get('route'),
    'linking': lambda x: f"{x.get('path')} -> {x.get('route')}",
    'storage': lambda x: x.get('key'),
    'analytics': lambda x: x.get('event'),
    'flags': lambda x: x.get('flag'),
    'native': lambda x: f"{x.get('module')} ({x.get('via')})",
}
LABEL = {'api': 'api', 'nav': 'route', 'linking': 'deeplink', 'storage': 'storage', 'analytics': 'analytics', 'flags': 'flag', 'native': 'native'}


def inventory(facts, scope=None):
    inside = defaultdict(lambda: defaultdict(list))
    outside = defaultdict(lambda: defaultdict(list))
    for f in facts['facts']:
        if f.get('test'):
            continue
        target = inside if scope is None or f['file'] in scope else outside
        for k, key in KINDS.items():
            for x in f.get(k, []):
                v = key(x)
                if v and v not in ('key', 'value', 'name', '?'):  # skip wrapper parameters
                    target[LABEL[k]][v].append(f"{f['file']}:{x.get('line')}")
    inv = {}
    for kind, items in inside.items():
        inv[kind] = {v: {'at': at, 'outside': outside[kind].get(v, [])} for v, at in sorted(items.items())}
    return inv


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if a and a[0] == 'diff':
        before, after = json.load(open(a[1]))['contracts'], json.load(open(a[2]))['contracts']
        broken = 0
        for kind in sorted(set(before) | set(after)):
            b, c = set(before.get(kind, {})), set(after.get(kind, {}))
            gone, new = sorted(b - c), sorted(c - b)
            if gone or new:
                print(f'{kind}: {len(gone)} removed, {len(new)} added')
                for v in gone:
                    print(f'  - {v}   (was {before[kind][v]["at"][0]}{"; still used outside: " + str(len(before[kind][v]["outside"])) if before[kind][v]["outside"] else ""})')
                for v in new:
                    print(f'  + {v}')
                broken += len(gone)
        print(f'\n{broken} contract(s) removed: each must be intentional (dropped from scope) or a regression')
        sys.exit(1 if broken else 0)
    facts = json.load(open(a[0]))
    scope = {o['file'] for o in json.load(open(opt('--scope')))['owned']} if '--scope' in a else None
    inv = inventory(facts, scope)
    total = sum(len(v) for v in inv.values())
    print(f'contracts {"of the feature (" + str(len(scope)) + " owned files)" if scope else "of the whole app"}: {total}')
    for kind in ['api', 'route', 'deeplink', 'storage', 'analytics', 'flag', 'native']:
        items = inv.get(kind, {})
        if not items:
            continue
        shared = sum(1 for v in items.values() if v['outside'])
        print(f'\n{kind} ({len(items)}{f", {shared} shared with code outside the feature" if scope else ""})')
        for v, d in list(items.items())[:12]:
            print(f'  {v[:70]:<70} {d["at"][0]}{"  +" + str(len(d["at"]) - 1) if len(d["at"]) > 1 else ""}{"  [outside: " + str(len(d["outside"])) + "]" if d["outside"] else ""}')
        if len(items) > 12:
            print(f'  ... {len(items) - 12} more (--json for all)')
    if '--json' in a:
        json.dump({'scope': sorted(scope) if scope else None, 'contracts': inv}, open(opt('--json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
