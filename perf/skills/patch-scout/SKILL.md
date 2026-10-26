---
name: patch-scout
description: >
  Reuse other apps' fixes to React Native and RN libraries: a library of known patches (perf, crash, visual; first
  harvest: Bluesky's social-app) with what each fixes, when it helps, how it was measured and how to measure it.
  scripts/scout.mjs scans a repo's installed packages, dry-runs every patch, applies/reverts one locally in
  node_modules (pnpm-store safe), and hands it to perf-climb for a measured A/B; only a win gets adopted. Use when
  the user says "known patches", "what patches could help", "try Bluesky's patches", "patch library", "scan for
  fixes", "upstream fixes we're missing", "harvest patches from <repo>", or during a perf climb on an RN app when
  the profile points into RN/library native code (text layout, Yoga, image decode, SVG, fonts at launch).
last-updated: 2026-10-26
---

# patch-scout: try someone else's fix, keep it only if it measures

```bash
S=<this skill dir>/scripts/scout.mjs
node $S scan <repo> [--category perf]   # applies | conflicts | already in, per patch, for installed packages
node $S show <id>                       # catalogue entry + the original author's write-up
node $S try <id> <repo>                 # apply in node_modules, recorded in <repo>/.patch-scout (git-excluded)
node $S revert <id> <repo>
```

Library: `library/catalog.json` + `library/<source>/<patch>` (+ `.patch.md` write-up, + the source's LICENSE).
Paths in patches are relative to the package root. `tested` is the version it was written for; `scan` decides real
applicability by dry run, so a patch for 0.88.0 that applies cleanly to 0.88.2 shows `applies`.

## The loop (perf)

1. **Scan**, then keep only `applies` perf entries whose `helps_when` matches what this app does (a text-heavy feed
   for `rn-prepared-text-layout`, animated headers in screens for `screens-yoga-no-dirty-on-clone`, custom/WOFF2
   fonts for `expo-modules-core-no-font-enumeration`). Read the code to confirm before spending a build on it.
2. **Same build for both sides.** `needs: rn-from-source` patches only exist in the binary when React Native is
   compiled from source; scan prints the warning when the app links the prebuilt core. Then BOTH baseline and
   candidate are built with `RCT_USE_PREBUILT_RNCORE=0 pod install` (Expo: `buildReactNativeFromSource: true`), or
   the A/B compares from-source against prebuilt instead of patch against no patch. Release builds only.
3. **Baseline** with `perf-climb` (`perf.mjs bench <scenario>`): the scenario and metric come from the entry's
   `measure` (e.g. 30 s fling of a text list, main-thread time in `drawAttributedString:` via `perf.mjs profile`).
4. `scout.mjs try <id>`, rebuild (rnfp: native inputs changed, so it is a miss, as it should be), bench again,
   `perf.mjs compare`. One patch per move.
5. **Accept** (perf-climb `accept`) -> adopt it the repo's way: `npx patch-package <pkg>` or `pnpm patch <pkg>` /
   `patchedDependencies`, copying the `.patch.md` next to it. **Reject** -> `scout.mjs revert <id>` and log the
   number, so nobody retries it blind. Record the result in the catalogue entry (`results: [{repo, metric, before,
   after, verdict}]`): the library gets smarter per app.

Watch the side costs the write-ups name: `rn-prepared-text-layout` moves ~300 ms onto the JS thread in Bluesky's
measurement; `rn-scrollview-disable-recycling` costs mount time.

## Crash / bug / visual entries

Not A/B material. Report them when `scan` says `applies` AND the app shows the symptom (Sentry signature in
`helps_when`, a repro on the simulator). Adopt with the same patch tooling; verify with the repro flow.

## Growing the library

From any repo with a `patches/` dir (patch-package or pnpm `patchedDependencies`):
1. Check the license allows reuse (Bluesky: MIT, kept at `library/bluesky/LICENSE`).
2. Classify the native patches (a `codex-agent` run did Bluesky's in one pass; spot-check its numbers in the write-ups).
3. Copy `<patch>` + its write-up into `library/<source>/`, add `sources.<source>` with repo + commit, and one
   catalogue entry per patch: id, file, package, tested, platform, category, needs, what, helps_when, measured,
   measure {scenario, metric}, upstream. Mark files that mix several changes `MIXED` in `what`.
4. `node tests/scout.test.mjs` and `node $S scan <a repo that has the package>`.

Drop an entry when `upstream` lands in a release the apps use (`scan` then says `already in` or `conflicts`).

## Self-check

`node tests/scout.test.mjs`: scan/try/revert on a throwaway repo, and a hard-linked store file stays unchanged.
