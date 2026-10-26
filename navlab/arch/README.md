# arch/: evidence tools for architecture skills

All tools are read-only on the target repo except where they write under `<repo>/.navlab/`. Python is stdlib-only,
Node uses `../scan/node_modules` (@babel/parser) through `../scan/lib.mjs` (`makeResolver`, `parseCode`, `walk`).

| tool | output | used by |
|---|---|---|
| `facts.mjs <repo>` | `.navlab/facts.json` (schema below) | contracts-scan, ds-audit, data-layer-audit, safety-net, design-afresh model |
| `depgraph.mjs <repo> --entry f` | owned vs shared closure, cycles; `--features` for many | design-afresh, portfolio |
| `cochange.py` / `history()` | git hotspots, change clusters, hidden coupling | design-afresh (cross-check), portfolio |
| `replay.py` | design backtest on history | design-afresh, rn-native-migration |
| `portfolio.py` | every reached navigator: structure × history × predicted lag → move | app-architecture-steward |
| `ledger.py` | predictions per move, scored for calibration; `withdraw` | steward, migration |
| `program.py` | program state per surface (move, stage, evidence, links); stage checks; `status --zoom 0/1/2` budgeted bundles | native-port, steward, gate ratchet |
| `featuremodel.py` | explicit feature model; `check [--strict]` (one owner, `writers` warn in as-is) | design-afresh |
| `fitness.py` | CI fitness functions: navscan budget, import rules, single-writer rules, AGENTS rules 6-7; ratchet | steward §6, design-afresh §7 |

## facts.json schema

```jsonc
{ "repo": "...", "generated": "ISO", "files": 2814, "totals": { "api": 290, ... },
  "facts": [ {
    "file": "src/views/X.tsx", "loc": 120, "test": false,
    "components": [{ "name": "X", "line": 10, "exported": true }],
    "jsx": { "Button": { "src": "src/components/Button.tsx" | "react-native" | "local", "count": 3, "props": { "title": 3, "onPress": 3 } } },
    "styles": { "colors": { "#fff": 2 }, "numbers": 5, "tokenRefs": 9 },     // literals vs theme/token references
    "api": [{ "kind": "fetch|http|gql|relay|gql-ref", "method": "GET", "op": "mutation", "target": "/users/:id | OpName", "client": "api", "line": 1 }],
    "nav": [{ "call": "navigate|push|replace|reset|jumpTo|popTo", "route": "Checkout | Routes.X", "line": 1 }],
    "linking": [{ "route": "Product", "path": "product/:id", "line": 1 }],     // deep links from linking config
    "storage": [{ "api": "AsyncStorage.setItem | <wrapper>", "key": "token", "line": 1 }],
    "analytics": [{ "call": "logEvent", "event": "EventType.X", "line": 1 }],
    "flags": [{ "call": "useFeatureFlag", "flag": "creditScore", "line": 1 }],
    "native": [{ "module": "RNKeychain", "via": "NativeModules | requireNativeComponent | TurboModuleRegistry.get", "line": 1 }],
    "state": { "reads": [{ "hook": "useSelector|useStores|useContext|use*Store|useQuery...", "arg": "...", "line": 1 }], "writes": 3 },
    "effects": [{ "deps": "none|empty|list|unstable|expr", "fetch": true, "line": 1 }],
    "waterfalls": [{ "line": 1, "awaits": [3, 4] }],                          // >= 2 sequential API awaits in one async fn
    "testIDs": ["home.push"],
    "imports": ["react-native-reanimated"]                                    // package imports only
  } ] }
```
Codebase conventions: `navlab.adapter.json → facts: { analytics: [], flags: [], storage: [], apiClients: [], designSystem: [] }`.

## Conventions for a new tool / skill
- One tool per question; print a short human summary and support `--json out`. Every finding carries `file:line`.
- Test on real repos before calling it done: at least one large app (thousands of files, Relay/MobX, e2e suites, no git),
  one RN Navigation v5 + GraphQL app, one monorepo app with node_modules installed, and one small app. Never write into those repos; use `--json` to a scratch path.
- A skill lives in `skills/<name>/SKILL.md` with `$NAVLAB_HOME` for the pack path; frontmatter `name` + one-line
  `description` (quoted, no colon-space inside unquoted values) with trigger phrases.
