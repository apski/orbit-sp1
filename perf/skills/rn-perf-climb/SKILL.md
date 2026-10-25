---
name: rn-perf-climb
description: >
  Hill-climb a React Native / Expo app's performance with argent and the perf-climb harness:
  perf.ts marks read over the Hermes debugger, request counts, React commit/render counts, React +
  native profiling, and a ratcheted baseline. Use when the codebase is React Native and the user
  says "make the app faster", "perf climb", "startup time", "screen opens slowly", "scroll jank",
  "jumpy scroll", "double fetch", or perf-climb detect returns rn.
last-updated: 2026-10-17
---

# RN Perf Climb

The loop, the config schema, and the accept rules live in **`perf-climb`**. Read it first. This
skill covers the React Native specifics.

`H="node ~/.claude/skills/perf-climb/scripts/perf.mjs"` · drop-in `~/.claude/skills/rn-perf-climb/assets/perf.ts`
· where bugs hide: `~/.claude/skills/rn-perf-climb/references/hunting-ground.md`.
· what to move off the JS thread and how: `thread-offload`.

Builds on argent's `argent-react-native-profiler`, `argent-native-profiler` and `argent-create-flow`.
Read those for tool detail.

## RN-specific metrics

| Metric | Source |
|---|---|
| spans | `mark()` from perf.ts (`performance.now()`), read via `debugger-evaluate` |
| `count:net:<METHOD> <path>` | perf.ts wraps `fetch`; ids collapse to `:id` |
| `react:commits`, `react:fiber_renders`, `react:hot_commits` | `"react": true` adds 3 extra reps under `react-profiler` (timings come from the plain reps) |

## Setup

1. **Environment.** Booted simulator with a window. Metro in **watch mode** (not `CI=1`, which
   serves a stale bundle). For reported numbers, use a release-like bundle
   (`expo start --no-dev --minify`, or a dev=false bundle). Dev timings are about 3× inflated but
   still show direction, and the harness never compares dev runs with release runs.
2. **perf.ts.** Copy it into the app and import it **first** in the entry file
   (`import './src/perf'` above `registerRootComponent`).
3. **Marks:**
   - startup: `["@launch", "app:interactive"]`, with `mark('app:interactive')` where the first real
     content is laid out. The `js:start` mark is automatic, so `["js:start", "app:interactive"]` is
     the JS-only part.
   - screen open: `mark('chat:press')` in the press handler, and `mark('chat:ready')` in
     `onContentSizeChange` / `onLayout` once data is present.
   - pagination: `older:request` → `older:rendered`. Scroll jumps: `count('scroll:jump')`, see
     hunting-ground §3.
   - pinned input size: `globalThis.__PERF__.counts['fixture:messages'] = n`.
4. **Flows.** RN `testID`s resolve in flow selectors. `setup` starts with `launch:`. In Expo Go,
   use `launch: host.exp.Exponent` then `- tool: open-url` with
   `args: { url: "exp://127.0.0.1:8081" }`. Keep `launch:` out of the measured flow when using
   `react: true` or `profile`.
5. **Config:** see perf-climb §3. RN needs no `bundleId`. Set `appProcess` (e.g. `"Expo Go"`) if
   the native profiler can't pick the app.

## Levels 1 and 2 (see perf-climb §2b)

- **Mountable subsystem:** a component that takes data (`<ChatList msgs={…}/>`) in its own module,
  plus `src/perfFixture.ts` for the pinned data. Put `count('render:Row')` in the row component.
- **Level 1, `assets/perfBench.tsx`:** copy it to `perf/bench/` and write
  `perf/bench/<x>.perf.test.tsx` that calls `benchComponent('chat_mount', () => <ChatList msgs={messages(3000)}/>)`.
  Needs `npx expo install jest-expo @testing-library/react-native -- --save-dev` and
  `"jest": { "preset": "jest-expo" }`. Scenario: `{ "cmd": "npx jest perf/bench" }`. About 2s per bench.
- **Level 2, `assets/perfRoutes.tsx`:** copy it to `src/`, fill in `ROUTES`, wrap the app in
  `PerfRouteHost` (falling back to `Fragment`), and add the Metro stub from the file header. Run
  Metro with `PERF_ROUTES=1` for perf work. Scenario: `{ "route": "chat?n=3000", "until": "chat:ready", … }`.
  The harness calls `globalThis.__perfRoute` over the debugger. About 6s per bench.
- **Release check:** run `npx expo export --platform ios --output-dir dist` without `PERF_ROUTES`,
  then `$H leakcheck dist`, which must be clean.

## Finding the gap

`$H profile <scenario>` writes `react.md`, `native.md` and `combined.md`. In `react.md`, read the
hot commits, the mount cascades (`Row ×60` mounted when 12 are visible), the "Top Components"
table and the per-commit CPU. Drill down with `profiler-commit-query by_index`,
`profiler-cpu-query component_cpu|call_tree` and `profiler-stack-query hang_stacks`. The React
profiler can't see app init. For startup, use the spans, the import graph and the native profile.

## RN rules

- React Compiler on (`react.md` header): don't add `memo`/`useMemo`/`useCallback` without a
  confirmed bail-out (no `useMemoCache` in `react-profiler-fiber-tree`).
- StrictMode double effects in dev are not a bug. Confirm a double fetch with `--no-dev`.

## RN gotchas (verified 2026-10-17, argent 0.25, Expo SDK 57)

- `debugger-reload-metro` breaks Expo Go ("Cannot find native module ExpoFetchModule"). Relaunch
  through the setup flow instead.
- On first load, Expo Go's dev-menu sheet swallows taps. Run
  `xcrun simctl spawn <udid> defaults write host.exp.Exponent EXDevMenuIsOnboardingFinished -bool YES`
  once.
- `react-profiler-start` needs a live runtime. A relaunch mid-recording kills the session.
- npm's 2-day release-age guard (`before` in npmrc) blocks the newest Expo template; use
  `--template blank-typescript@sdk-<prev>`.

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: the loop's rules are `perf-climb`'s (accept = gate, accepted baseline = floor, `perf/ledger.jsonl` = log). RN-specific: a ruler change (new `mark`/`count`/`fixture`) is its own re-baseline move, never folded into a fix.
