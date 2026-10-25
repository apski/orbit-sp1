#!/usr/bin/env python3
"""datalayer: state and networking risks from facts: where screens fetch, refetch, wait in series and over-subscribe.

  datalayer.py <facts.json> [--scope depgraph.json] [--json out]

Patterns (each with file:line, ranked per file):
  refetch-risk   useEffect that fetches with no deps / unstable deps (object, array, inline function): fetches every render
  effect-fetch   fetch inside an effect with no cache layer in the file (no react-query/SWR/Apollo/Relay hook): refetch on every mount
  waterfall      >= 2 sequential awaits of API calls in one async function (not in Promise.all): latency adds up
  subscriptions  many store/context reads in one component file: broad subscriptions re-render on unrelated changes
  writes         dispatch/setState-style writes in screens (state ownership spread across the UI)
App level: which data libraries coexist (REST via fetch/axios, GraphQL via Apollo/urql, Relay, React Query, SWR) and which
state libraries (Redux, Zustand, MobX, Context, Recoil, Jotai): several of each is a boundary problem before a perf one.
Verify at runtime with navprobe counters (net:*, redux:*) on journeys before changing anything.
"""
import json, sys
from collections import Counter

CACHE_HOOKS = {'useQuery', 'useInfiniteQuery', 'useSWR', 'useLazyLoadQuery', 'usePreloadedQuery', 'useFragment', 'useLazyQuery'}
LIBS = {'@tanstack/react-query': 'react-query', 'react-query': 'react-query', 'swr': 'swr', '@apollo/client': 'apollo', 'urql': 'urql',
        'react-relay': 'relay', 'relay-runtime': 'relay', 'axios': 'axios', 'ky': 'ky', 'redux': 'redux', '@reduxjs/toolkit': 'redux',
        'react-redux': 'redux', 'zustand': 'zustand', 'mobx': 'mobx', 'mobx-react': 'mobx', 'mobx-react-lite': 'mobx', 'mobx-state-tree': 'mobx',
        'recoil': 'recoil', 'jotai': 'jotai', 'valtio': 'valtio', 'xstate': 'xstate', '@xstate/react': 'xstate'}


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    facts = json.load(open(a[0]))
    scope = {o['file'] for o in json.load(open(opt('--scope')))['owned']} if '--scope' in a else None
    libs, rows = Counter(), []
    fetch_files = 0
    for f in facts['facts']:
        if f.get('test'):
            continue
        for imp in f['imports']:
            for pkg, lib in LIBS.items():
                if imp == pkg or imp.startswith(pkg + '/'):
                    libs[lib] += 1
        if any(x['kind'] == 'fetch' for x in f['api']):
            fetch_files += 1
        if scope is not None and f['file'] not in scope:
            continue
        cached = any(r['hook'] in CACHE_HOOKS for r in f['state']['reads'])
        refetch = [e for e in f['effects'] if e['fetch'] and e['deps'] in ('none', 'unstable')]
        effect_fetch = [e for e in f['effects'] if e['fetch'] and not cached and e['deps'] not in ('none', 'unstable')]
        reads = [r for r in f['state']['reads'] if r['hook'] not in CACHE_HOOKS]
        score = 3 * len(refetch) + 2 * len(f['waterfalls']) + len(effect_fetch) + max(0, len(reads) - 4)
        if score:
            rows.append({'file': f['file'], 'score': score, 'refetch_risk': [e['line'] for e in refetch], 'effect_fetch': [e['line'] for e in effect_fetch],
                         'waterfalls': [w['line'] for w in f['waterfalls']], 'subscriptions': len(reads),
                         'hooks': Counter(r['hook'] for r in reads).most_common(4), 'writes': f['state']['writes'], 'api': len(f['api'])})
    if fetch_files:
        libs['fetch'] = fetch_files
    rows.sort(key=lambda r: -r['score'])
    data = sorted(k for k in libs if k in ('react-query', 'swr', 'apollo', 'urql', 'relay', 'axios', 'ky', 'fetch'))
    state = sorted(k for k in libs if k in ('redux', 'zustand', 'mobx', 'recoil', 'jotai', 'valtio', 'xstate'))
    print(f'datalayer {"(feature: " + str(len(scope)) + " files)" if scope else "(whole app)"}: {len(rows)} files with data-layer risk')
    print(f'  data libraries (files importing): ' + ', '.join(f'{k} {libs[k]}' for k in data))
    print(f'  state libraries (files importing): ' + (', '.join(f'{k} {libs[k]}' for k in state) or 'none (Context/useState only)'))
    caches = [k for k in data if k in ('react-query', 'swr', 'apollo', 'urql', 'relay')]
    if len(caches) > 1 or len(state) > 1:
        print(f'  -> {len(caches)} client cache layers / {len(state)} state libraries coexist: decide the target stack before tuning screens')
    tot = lambda k: sum(len(r[k]) if isinstance(r[k], list) else 0 for r in rows)
    print(f'  totals: refetch-risk effects {tot("refetch_risk")}, uncached effect fetches {tot("effect_fetch")}, waterfalls {tot("waterfalls")}, '
          f'files with > 4 subscriptions {sum(r["subscriptions"] > 4 for r in rows)}')
    print('\nranked (score: refetch x3, waterfall x2, uncached effect fetch, subscriptions beyond 4):')
    for r in rows[:15]:
        bits = []
        if r['refetch_risk']:
            bits.append(f'refetch-risk @{",".join(map(str, r["refetch_risk"]))}')
        if r['waterfalls']:
            bits.append(f'waterfall @{",".join(map(str, r["waterfalls"]))}')
        if r['effect_fetch']:
            bits.append(f'effect-fetch @{",".join(map(str, r["effect_fetch"]))}')
        if r['subscriptions'] > 4:
            bits.append(f'{r["subscriptions"]} subscriptions ({", ".join(h for h, _ in r["hooks"])})')
        print(f'  {r["score"]:3}  {r["file"]}  ' + '; '.join(bits))
    if '--json' in a:
        json.dump({'libraries': dict(libs), 'files': rows}, open(opt('--json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
