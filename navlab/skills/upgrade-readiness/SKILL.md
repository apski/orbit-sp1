---
name: upgrade-readiness
description: "Check whether the platform under a migration or redesign is stable: per dependency the installed vs latest version, deprecation, last publish and major lag (npm registry, cached), New Architecture readiness of every native package (ready / interop-only / unknown, with file:line evidence and whether the latest version fixes it), the app's newArchEnabled, React Native and Expo SDK lag including SDK version mismatches, and a ranked risk list. Use when the user says 'upgrade readiness', 'are our deps up to date', 'new architecture readiness', 'can we turn on new arch', 'which libraries are deprecated', 'RN upgrade', 'Expo SDK upgrade', 'unmaintained packages', or before a migration, redesign or RN upgrade."
---

# Upgrade readiness

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check upgrade-readiness` (the repo profile from the install; it asks only for what this skill is missing).

A redesign or migration built on a deprecated or legacy-only dependency inherits its risk: the next RN upgrade breaks
it mid-program. Check the platform first, and fix or plan around what blocks.

Tool: `$NAVLAB_HOME/arch/upgrade.py` (stdlib; read-only on the repo; `npm view` against the public registry only).

## Steps

1. Install deps if you can (`node_modules` is needed for installed versions and the New Arch scan; without it versions
   are declared ranges).
2. `python3 $NAVLAB_HOME/arch/upgrade.py <repo> --json .navlab/upgrade.json`
   - registry results are cached 7 days in `~/.cache/navlab/npm-view.json` (`--cache` to move it); `--offline` uses the
     cache only. Private scoped packages are listed as "not on the public registry".
3. Read top to bottom:
   - **react-native / expo:** minors behind (RN is 0.x), Expo SDK vs latest, and **SDK mismatch** lines: packages at a
     different version than the installed SDK expects (`expo/bundledNativeModules.json`, like `expo install --check`).
     An SDK that expects a different RN than installed is a half-done upgrade; finish it before anything else.
   - **newArchEnabled:** the app setting with `file:line`. RN ≥ 0.76 defaults it on; ≥ 0.82 is New Arch only, so
     `false` caps the RN version you can reach.
   - **native packages:** `ready` (codegenConfig, TurboModule/Fabric spec, `RCT_NEW_ARCH_ENABLED` code paths, Expo
     module), `interop-only` (legacy `RCT_EXPORT_*` / `@ReactMethod` / ViewManager, evidence `file:line`), `unknown`.
     `[latest: codegen]` = upgrading fixes it; `[latest: no codegenConfig]` = read its changelog, else replace or wrap.
   - **risks, ranked:** deprecated > non-registry (git) deps > legacy-only native > unmaintained (latest publish > 2y;
     native weighs double) > major lag. Dev deps count half.
4. Turn the top risks into a plan: upgrade (latest is ready), replace (deprecated / renamed / unmaintained native),
   wrap behind a seam (no replacement), or accept with a reason. Order: SDK mismatches, then deprecated native, then
   legacy-only on hot paths.

## Feeding other skills

- **app-architecture-steward:** the `upgrade` move. Put it before redesign/migrate moves on features that import a
  top-ranked package. Ledger metrics (`--metric` prints one number last): `legacy` (interop-only native packages),
  `deprecated`, `risky` (score ≥ 30). Example:
  `ledger.py add --move upgrade --metric legacy --baseline 29 --predicted 10 ... --measure "python3 $NAVLAB_HOME/arch/upgrade.py . --offline --metric legacy"`.
- **rn-native-migration:** a surface whose native deps are legacy-only pays an upgrade on top of the port; count it in
  the cost. A deprecated OTA library also changes the release model (see `release-boundary`).
- **design-afresh:** legacy-only modules on the feature's seam are candidates for a typed native boundary in the new shape.

## Limits

- Readiness is static text matching on the installed version: a package can ship a spec and still misbehave, or support
  the New Arch through C++ with no marker. Verify the top risks by building with New Arch on.
- "Unmaintained" is the publish date of the latest version; a finished small library can be fine.
- Lag counts majors (minors for 0.x); it says nothing about breaking-change size.
- `latest` is the registry's `latest` dist-tag; deprecation is read for that version only.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill upgrade-readiness --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
