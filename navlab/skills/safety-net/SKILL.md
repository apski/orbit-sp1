---
name: safety-net
description: "Before a redesign, refactor or RN/native migration, check whether an e2e safety net actually pins down the journeys that touch the surface: find the Maestro, Detox, Cypress, Appium, argent and simwalk suites, map what each flow references (testIDs, visible text, deep links) onto the app's screens, and list uncovered screens, skipped or never-run-in-CI suites, unused testIDs and stale tests; then fill the gaps with argent-qa-flows. Use when the user says 'is it safe to change', 'do we have e2e coverage', 'safety net', 'which screens are tested', 'stale tests', 'before we refactor', 'before we migrate', or as step 0 of design-afresh and rn-native-migration."
---

# Safety net

A redesign is only as safe as the tests that would notice it broke something. Unit tests rarely span a journey; e2e
flows do, but suites rot: skipped files, flows CI never selects, ids renamed in code. This skill answers **which screens
of the surface would a broken journey actually fail a test on, today?**

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check safety-net`. No suites at all is a valid answer: the gap list
below is then the whole surface, and the first deliverable is the flows.

```bash
node $NAVLAB_HOME/arch/facts.mjs . --json .navlab/facts.json
node $NAVLAB_HOME/scan/navscan.mjs . --json .navlab/navscan.json
node $NAVLAB_HOME/arch/depgraph.mjs . --entry <Feature>Navigator.tsx --json .navlab/<f>-dep.json        # optional scope
python3 $NAVLAB_HOME/arch/safetynet.py . --facts .navlab/facts.json --navscan .navlab/navscan.json \
    [--scope .navlab/<f>-dep.json] --json .navlab/<f>-safetynet.json
```
Scope with the same depgraph file design-afresh / rn-native-migration use, so "the surface" means the same files.

## Reading it
Per screen (navscan route → component file → its private children):
- **covered**: an active flow references a distinctive testID (`id:`), distinctive copy (`text:`, shown by ≤ 2 files),
  or a deep link that resolves to this route (`link:`). Check the flow also *asserts* something there; a tap-through
  is weaker than an `assertVisible`/`await`.
- **weak**: only `id~` hits: generic ids (`continue-button`, defined in > 2 files), template ids (`nav-tab-${id}`), or
  ids from shared components the screen renders. Treat as uncovered for gating.
- **skipped-only**: only `describe.skip`/`xdescribe` files reach it. Reviving the file may be cheaper than a new flow.
- **uncovered**: nothing reaches it.
- Flow flags: `[skip]`; `[no CI]` (the suite never runs in CI); `[CI: manual tags]` (Maestro tags only selected by a
  dispatch-time `TAGS=${{ inputs.tags }}` job). Maestro CI selection is read from `TAGS=`/`--include-tags` and the
  `EXCLUDETAGS` set next to it.
- **stale flow refs**: ids a flow uses that exist nowhere in code (not as testID, template or string). Fix before
  trusting that suite. Android resource ids (`pkg:id/x`) are listed separately, not stale.
- **testIDs never used**: free stable selectors for new flows.
- **not app-facing** (Cypress): flows that reference nothing in the app (mail catchers, web dashboards). Not coverage.

## Filling the gaps (argent-qa-flows)
Work the `gaps` list in journey order, hot screens first (portfolio / usage):
1. Screens marked `NONE: add testIDs first` (or `shared only`): add a root `testID="<screen>-screen"` and ids on the
   actions the journey takes. One small PR, no behaviour change.
2. One QA flow per user journey (not per screen) with the `argent-qa-flows` skill: start from `entry:` (the deep link
   when there is one, else walk from launch), `await` each screen's root id, assert the outcome. Save under
   `.argent/flows/<journey>.yaml` so this tool counts it.
3. Revive `skipped-only` suites and fix stale ids where that is cheaper than a new flow.
4. Re-run safetynet until every in-scope screen is `covered` by a flow CI runs, or the gap is accepted in writing.

## Where it feeds
- `design-afresh` §7 and `rn-native-migration` step 0: the move starts only when its surface is covered. The
  coverage table goes into the design doc.
- `rn-native-migration`: a native rewrite keeps the ids: `accessibilityIdentifier` = the RN `testID`, so the same
  flows judge both sides. Flows that pass on RN and fail on native are migration bugs, found before users find them.
- `native-port`: readiness item 5. `program.py set <surface> safety=.navlab/<f>-safetynet.json` (at least one screen covered).
- `app-architecture-steward`: "is it safe to change?" column; `contracts-scan` supplies what the flows should assert
  (events fired, keys persisted, deep links resolving).

## Limits (heuristic, static)
- Screen → component resolution is regex over `<X.Screen name component>` plus import/alias/barrel resolution;
  screens built by helper functions with components passed as arguments stay unresolved (shown as such).
- A screen's "own" ids are its file plus relative imports (and, with `--scope`, feature-owned imports), depth 2. Ids
  passed down as props from a parent screen are attributed to the parent.
- Coverage means "a flow references something only this screen shows", not "a flow asserts the screen works".
- Text matching needs literal copy in code; i18n keys and server-driven copy are invisible (ids still work).
- Whole-file skips only; `it.skip` inside an active file still counts. Assertions of absence (`assertNotVisible`)
  count as references. CI detection reads GitHub/CircleCI/Bitrise/GitLab/EAS config text, not dynamic matrices.
- Tested on bt-onboarding (Maestro + Detox + Cypress) and a repo with no suites; the Appium, argent and simwalk
  extractors are checked only on small synthetic files, not a real suite.
- `python3 $NAVLAB_HOME/arch/safetynet.py --selftest` checks deep-link and template-id matching.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill safety-net --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
