---
name: contracts-scan
description: "Inventory the contracts a React Native feature or app exposes and depends on (REST and GraphQL/Relay operations, navigation routes, deep links, persisted storage keys, analytics events, feature flags, native modules), show which are shared with code outside the feature, and diff two inventories to prove a redesign or migration lost nothing. Use when the user says 'what does this feature depend on', 'contracts', 'what must not break', 'storage keys', 'analytics events', 'deep links', 'before we migrate', 'did we break anything', or as the scope step of design-afresh and rn-native-migration."
---

# Contracts scan

A redesign or migration can be faster and cleaner and still break users: a renamed storage key logs people out, a
dropped event blinds the funnel, a moved deep link 404s from old emails. Contracts are what must survive.

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check contracts-scan`. The repo profile tells the extractor about
the codebase's own analytics, flag and storage wrappers (`navlab.adapter.json → facts`); without them, events and keys
behind wrappers show up as `key`/`event` parameters only.

```bash
node $NAVLAB_HOME/arch/facts.mjs .                                                        # .navlab/facts.json
python3 $NAVLAB_HOME/arch/contracts.py .navlab/facts.json [--scope .navlab/<f>-dep.json] --json .navlab/<f>-contracts.json
python3 $NAVLAB_HOME/arch/contracts.py diff before.json after.json                       # exit 1 if anything disappeared
```

## Reading it
- **`[outside: N]`**: the contract is also used by N places outside the feature. Changing it ripples, so treat it as
  a shared interface. In a migration it needs a bridge, or stays on one side.
- **api:** `gql:mutation X` is defined in this feature; `gql-ref:useQuery X` is used here. REST paths have ids collapsed to `:id`.
- **route:** navigation targets called from the feature (`navigate/push/...`). The feature's *own* screens are in navscan's tree.
- **storage:** keys persisted on device. Changing one is a data migration, not a refactor.

## Where it feeds
- `design-afresh`: `featuremodel.py check` fails while any in-scope contract is unaccounted for.
- `rn-native-migration`: contracts with `[outside]` users that cross the proposed line become bridge APIs; count them in the boundary tax.
- `safety-net`: contracts are the test oracle (events fired, keys read, deep links resolve).
- CI: run `diff` against the base branch on PRs that touch a redesigned feature.

## Limits
Static and heuristic. Dynamic keys and event names show as expressions. Server-side contracts (GraphQL schema,
REST shape) are only seen from the client side. Pair with the backend repo when the feature's hidden coupling
(cochange) points there.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill contracts-scan --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
