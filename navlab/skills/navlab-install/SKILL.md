---
name: navlab-install
description: "Install the NavLab pack (every architecture skill) into this React Native repo and keep it fitted to the codebase: one repo profile that every skill reads (analytics/flag/storage wrappers, API clients, design-system roots, native DS, e2e suites, OTA, telemetry, git), the navscan adapter, navprobe wiring with Instruments signposts and journeys, and discovery of what else needs instrumenting. Use when the user says 'install navlab', 'set up the lab', 'install the skills', 'wire the probe', 'instrument navigation', 'instrument state or networking', 'navscan has unresolved items', 'update the adapter', a skill's preflight fails, or after the codebase changed shape."
---

# NavLab install (the whole pack)

Rules, mechanism and baseline live in `$NAVLAB_HOME/AGENTS.md`. Read it first. This skill fits **every** pack skill to
*this* codebase and keeps it fitted. There is one install and one repo profile; each skill starts with
`python3 $NAVLAB_HOME/arch/profile.py check <skill>` and only asks for what its own section is missing. Everything you learn about the codebase goes into `navlab.adapter.json` (navscan)
or `navlab.config.json` (`instrumented` / `gaps`), never only into chat.

## 0. Bootstrap (once)

`node $NAVLAB_HOME/bootstrap.mjs .` writes config, the adapter, `.navlab/navscan.json`, these skills and an
AGENTS.md block. It writes no app code. Re-run it after dependency or iOS project changes.

## 0b. Repo profile (every skill reads it)

```bash
python3 $NAVLAB_HOME/arch/profile.py detect .        # bootstrap already ran this once
for s in navlab-audit design-afresh contracts-scan ds-audit data-layer-audit safety-net release-boundary field-perf upgrade-readiness rn-native-migration app-architecture-steward; do
  python3 $NAVLAB_HOME/arch/profile.py check $s; done   # readiness per skill
```
- **Detected** sections are guesses with evidence: analytics/flag/storage wrapper functions, API clients, design-system
  packages and folders, the native DS folder, e2e suites, OTA, telemetry, git history.
- Show the user the detected values **in one message** and ask them to confirm or correct each. Fix wrong values in
  `navlab.config.json → profile` / `navlab.adapter.json → facts`, then run `profile.py confirm <section>`.
- **Missing** sections come with the question to ask (usage export, Swift ownership, native DS path). Skills still
  run without the optional ones and say what that costs.
- After changing facts conventions, re-run `node $NAVLAB_HOME/arch/facts.mjs .`. Verified effect on a real app: confirming
  the analytics and storage wrappers raised the extracted events 73 → 104 and storage keys 25 → 47.

## 1. Adapter loop: make navscan see the real tree

```bash
node $NAVLAB_HOME/scan/navscan.mjs . --learn --top 20
```
Repeat until `unresolved` only holds items you've decided to leave:

| unresolved kind | what to do |
|---|---|
| `dynamic-screen-list` | Read the array it maps over and set `screenLists["file#expr"] = N` (the route count). |
| `unknown-navigator` | Find the factory or wrapper (`createAppStack()`, a navigator built by a function) and set `factories["name"] = "stack"\|"tabs"`. |
| `unresolved-screen` | Fine unless that screen mounts a nested navigator. If it does, `screenNavigators["<parent node id>>Route"] = "<child node id>"` (ids are printed in the tree). |
| `parse-error` | Non-app code (svg/json, generated code): add to `ignore`. |
| NOT REACHED FROM ENTRY | Confirm it's dead (grep usages). If it's mounted dynamically (registry, deep link host), wire it with `screenNavigators`. |

Then **check the tree against the app**: tab counts, auth-switched roots, modal stacks. One quick look in the
simulator, or a human, confirms it. A scanner that is confidently wrong is worse than none.

If a codebase pattern can't be expressed in the adapter, improve `$NAVLAB_HOME/scan/navscan.mjs` itself. Add the
smallest fixture repro under `$NAVLAB_HOME/scan/fixtures/` and keep the other repos' scans unchanged.
Patterns already handled: default-exported navigator instances, tsconfig `baseUrl`/`paths` and babel
module-resolver aliases, HOCs (`withA(withB(observer(X)))`), screen helper functions with argument binding
(`getScreens({ X })`), `.map` screen lists, conditional screens/components, `export default from`, the v4/v7
static configs, Expo Router file trees, and entry reachability (dead navigators).

## 2. Wire navprobe (runtime shape, per-action timing, journeys)

1. Copy `$NAVLAB_HOME/probe/navprobe.ts` to `src/navlab/navprobe.ts` (or the repo's source root). Copy the model too:
   `$NAVLAB_HOME/baseline/<target>/model.json` -> `src/navlab/model.json`.
2. Attach it where the container lives (`navlab.config.json` → `navigationContainers`):
   ```tsx
   const navRef = createNavigationContainerRef();               // reuse the app's ref if it has one
   <NavigationContainer ref={navRef} onReady={() => navlabOn && attachNavProbe(navRef, { model, signposts, journeys })}>
   ```
   With Expo Router, use `useNavigationContainerRef()` in the root `_layout`, and attach in an effect once `isReady()`.
   Gate it: `const navlabOn = __DEV__ || Settings.get('navprobe') === '1'`. Release runs opt in with `-navprobe 1`.
3. Keep the diff small and reviewable. Don't change navigation behaviour; never ship it enabled by default.

## 3. Signposts for Release timing (Lab native module)

- **Expo or expo-modules-core present:** copy `$NAVLAB_HOME/app/modules/lab` to `modules/lab` and run `pod install`
  (Expo autolinks `modules/`). Pass `signposts: requireOptionalNativeModule('Lab')`. It provides begin/end/event/exit.
- **Bare RN without expo-modules-core:** ask before running `npx install-expo-modules@latest`. Without it, run
  shape-only (navprobe summary over the debugger). Timings from a Debug build are meaningless.
- Verify that `xcrun xctrace export ... os-signpost` shows `navaction` intervals for subsystem `lab`.

## 4. Journeys (measured runs without taps)

Write `navlab.journeys.json` with real route names from the navscan tree. Cover:
- the deepest stacks
- every big tab or drawer navigator
- the 3–5 core user flows
- one long push chain (stack growth)

Params must be valid: read the ParamList types. A journey that crashes or bounces to auth measures nothing.
Run with `python3 $NAVLAB_HOME/lab/journey.py <name>`.

## 5. Discover what else to instrument (state, networking, lists, startup)

Navigation is one cause among several. Read `navlab.config.json → detected` and record per item in
`instrumented` / `gaps`:

| detected | wire | why |
|---|---|---|
| fetch (always) | `instrumentFetch()` at startup | duplicate fetches, waterfalls on screen open |
| axios | `instrumentAxios(apiClient)` per instance | same |
| @tanstack/react-query | `queryClient.getQueryCache().subscribe(e => count('rq:' + e.type))` | refetch storms on focus |
| @apollo/client | an ApolloLink calling `count('gql:' + operationName)` | over-fetching per screen |
| redux / RTK | `navprobeReduxMiddleware` in the middleware list | action storms, slow reducers on navigation |
| zustand | `store.subscribe(() => count('zustand:<store>'))` | store churn re-rendering everything |
| mobx | `spy(e => e.type === 'reaction' && count('mobx:reaction'))` (dev only) | reaction cascades |
| React context | `<React.Profiler id="Providers">` around the provider stack (dev) | provider re-renders on every navigation |
| lists | `count('render:<Row>')` in hot row components | mount cascades (see rn-perf-climb hunting ground) |
| startup | `mark('js:start')` in the entry, `mark('app:interactive')` on first real content | startup budget |
| JS-thread work | `span('<name>', fn)` around heavy synchronous work | blocking the JS thread during transitions |

Verify each one moves a counter in `__NAVPROBE__.summary()` (argent `debugger-evaluate`) before calling it done.

## 6. Verify the install

Release build → `journey.py smoke` (or the shortest real journey). Check that the trace has `navaction` intervals,
that the summary is non-empty, that the typecheck passes, and that the app behaves the same with the probe off
(run the repo's e2e or an argent flow). Report what is wired, what is still a gap, and the adapter decisions.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill navlab-install --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
