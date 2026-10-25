---
name: data-layer-audit
description: "Audit a React Native app's state management and networking: which data and state stacks coexist (REST, GraphQL, Relay, React Query, SWR; Redux, Zustand, MobX, Context, Recoil, Jotai), effects that refetch on every render, uncached fetches, request waterfalls, broad store subscriptions, then confirm at runtime with navprobe counters and fix through the perf loop. Use when the user says 'data layer', 'state management audit', 'double fetch', 'waterfall', 'too many requests', 'render storm', 'store re-renders', 'which state library', or when navlab-audit shows large unexplained cost on screen opens."
---

# Data layer audit

After navigation structure and screen weight, the data layer is the usual source of "unexplained" cost in a journey:
a screen that fetches twice, waits for three calls in series, or re-renders on every store change.

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check data-layer-audit` (API client names, state libraries).

```bash
node $NAVLAB_HOME/arch/facts.mjs .
python3 $NAVLAB_HOME/arch/datalayer.py .navlab/facts.json [--scope .navlab/<f>-dep.json] --json .navlab/datalayer.json
```

## Reading it
- **Coexisting stacks:** more than one client cache layer (Apollo + React Query, Relay + SWR) or more than one state
  library is an architecture decision to make before tuning screens. REST + GraphQL against different backends is normal.
- **refetch-risk:** an effect that fetches with no deps or unstable deps (an object, array or inline function) runs
  every render. It's usually a real double fetch.
- **effect-fetch:** a fetch in an effect with no cache hook in the file. It refetches on every mount and on
  switch-back (rn-perf-climb hunting ground §2, §4).
- **waterfall:** two or more sequential awaits of independent calls. Check that they really are independent, then use
  `Promise.all` (verified example: account info, then APR, in one store).
- **subscriptions:** more than 4 store/context reads in one component file. Broad subscriptions re-render on unrelated changes.

## Confirm before fixing (static findings are hypotheses)
1. Wire navprobe counters (navlab-install §5: `instrumentFetch`, `instrumentAxios`, `navprobeReduxMiddleware`, store subscribes).
2. Run the journey that opens the screen (`journey.py`, or argent in Debug for counts). `net:*` ≥ 2 for one endpoint
   confirms a double fetch; `redux:*` storms confirm churn.
3. Fix in `rn-perf-climb` (one move, measured, accepted by the bench), or restructure in `design-afresh` when the
   ownership itself is wrong (several writers for one piece of state).

## Where it feeds
- The app-level stack decision goes into `app-architecture-steward` as a move with ledger metrics: requests per journey, renders per update.
- `rn-native-migration`: state read on both sides of a proposed line is a sync problem. Count it in the boundary tax.
- New labs: `mobile-architecture-lab` references/subsystems.md §state, §networking when a cost needs a curve.

## Limits
Static heuristics (MobX `observer` re-renders and selector equality aren't analyzed). Counts come from call
sites, not runtime frequency; the probe is the judge.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill data-layer-audit --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
