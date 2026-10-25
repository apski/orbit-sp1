---
name: release-boundary
description: "Measure what the JS/native split costs at release time: classify every commit in git history as JS-only (OTA-able), native-only or both (crosses the seam), report the share needing a store release and cross-seam commits per month, detect the OTA setup (expo-updates, CodePush, ota-hot-update) and its runtimeVersion policy with what it means for version skew, and report bundle/binary size from any build on disk. Feeds the boundary tax in rn-native-migration. Use when the user says 'release cost', 'how often do we need a store release', 'what can we ship OTA', 'boundary tax', 'version skew', 'runtimeVersion', 'is CodePush safe', 'OTA vs store', or before moving a surface across the RN/native boundary."
---

# Release boundary

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check release-boundary` (the repo profile from the install; it asks only for what this skill is missing).

Every change that touches native code waits for store review and for users to update; JS-only changes can ship over
the air if OTA is set up. A change that touches **both** is the expensive kind: native first, then a version-gated JS
update. This skill measures how often each happens, so the boundary tax is a number, not a guess.

Tool: `$NAVLAB_HOME/arch/release.py` (read-only, stdlib). It uses `cochange.history()`, so a monorepo app or a nested
`.git` with the real history in the parent both work.

## Steps

1. `python3 $NAVLAB_HOME/arch/release.py <repo> --since "2 years ago" --json .navlab/release.json`
   If it says "no app commits since …", widen `--since` to the date it prints. Without git it still reports OTA and sizes.
2. Read the split:
   - **needs a store release** = native-only + both. Shown as a range when a dependency change couldn't be resolved
     (no node_modules: "unknown native?", `*` = the name suggests native). Install deps to narrow it.
   - **boundary tax** = both-side commits/month. The seam files list shows *where* the JS↔native coupling lives
     (AppDelegate/MainApplication, a bridge module, Podfile.lock from native deps).
   - version-bump-only commits (Info.plist / pbxproj / gradle version lines) are counted as releases, not native work.
3. Read the OTA block: library, runtimeVersion policy with `file:line`, and the skew note.
   - **none:** everything ships via the store. The JS-only share is what adopting OTA would buy.
   - **fingerprint / nativeVersion:** safe skew; check how many runtimes are live.
   - **appVersion / CodePush / fixed string:** a native change without a version bump lets new JS reach an old binary.
     Recommend a CI check: native files changed ⇒ runtime/binary version changed.
4. Sizes: JS bundle (Hermes bytecode or plain JS), `.app`/`.ipa`/`.apk`/`.aab` if built. "no build found" means build
   Release first if size matters to the decision.

## Feeding other skills

- **rn-native-migration §2–3:** use `both_per_month` as the current cross-boundary rate and `store_per_month` as the
  release load. Moving a surface to native turns its JS-only commits into store releases: forecast that with
  `replay.py` on the split map, and compare.
- **app-architecture-steward ledger:** metrics with a measure command (`--metric` prints one number last):
  `--metric both_per_month` (target down after a seam redesign), `--metric store_share`, `--metric ota_share`.
  Example: `ledger.py add --move migrate --metric both_per_month --measure "python3 $NAVLAB_HOME/arch/release.py . --since '6 months ago' --metric both_per_month" ...`
- **upgrade-readiness:** a deprecated OTA library (CodePush after App Center's retirement) shows up there as a risk.

## Limits

- File-path heuristics: a JS commit that only works with a native change shipped earlier counts as JS-only.
- `package.json` checks `dependencies` only; lockfile-only bumps of native transitive deps are not seen.
- `app.config.*` changes are always treated as native (dynamic config can't be diffed safely).
- `.env*` counts as native only when react-native-config is a dependency (it bakes values into the build).
- Commits touching > 40 files are dropped by `history()` (codemods), so big upgrades may be undercounted.
- The policy parser reads app.json / app.config / Expo.plist / strings.xml text; computed runtime versions need a manual look.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill release-boundary --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
