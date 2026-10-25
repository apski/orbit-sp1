---
name: spectra-bootstrap
description: "L1 bootstrap for Spectra (RN / Figma -> native UI compiler from orbit-SP1): fit it to THIS React Native repo before any conversion. Confirms the repo profile (RN version, styling, jest, screens, design system, native target, fonts, Figma), makes the jest capture harness render one real screen, and proves the loop end to end on that screen. Use when the user says 'install spectra', 'set up spectra', 'bootstrap', 'fit spectra to this repo', after `spectra install`, when `spectra check` reports NOT READY, or when a capture fails on mocks/providers."
---

# Spectra bootstrap (L1)

Spectra is mostly deterministic code; this skill is the part that needs judgement: reading the repo, deciding, and
writing the few repo-specific files the code can't guess. Everything you learn goes into `.spectra/` files, never only
into chat, so the next run (and the next agent) starts from it.

CLI: `.spectra/spectra <command>` (written by install; run with no args to list commands). If `.spectra/spectra` is
missing, install first: `node <orbit-SP1>/spectra/bin/spectra.mjs install .`.

## 1. Profile: confirm what was detected (one message to the user)

```bash
.spectra/spectra detect            # re-scan; confirmed sections are kept
.spectra/spectra check             # readiness per step: capture-jest, capture-runtime, figma, gen, verify
```

- Show the user the **detected** sections in one message (value + evidence) and ask them to confirm or correct each.
  Correct values by editing `.spectra/profile.json`, then `.spectra/spectra confirm <section>`.
- **missing** sections carry the question to ask (`ask`). Ask only what the user's goal needs: Figma-only work needs
  `figmaToken`, not `jest`.
- `simulator`: if several are booted, ask which one Spectra may use; others may belong to other sessions or people.
  Record it as the confirmed value and pass `--udid` to `verify`.
- Notes matter: NativeWind (jest needs `react-native-css-interop`'s jest setup), iOS target below 26 (generated text uses
  `.lineHeight`; flag it rather than silently changing output).

## 2. Jest harness: make one real screen render

The harness is `.spectra/jest/` (install wrote it from templates): `jest.config.cjs` (the app's preset and Babel),
`setup.js` (native-module mocks), `wrap.js` (providers and props per screen). Pick the first screen with the user
(profile `screens`), then loop until it captures:

```bash
.spectra/spectra capture jest <path/to/File.tsx> <ExportName>      # writes .spectra/ir/<Export>.jest.json
```

| Failure | Fix (one at a time, re-run after each) |
|---|---|
| `Unexpected token 'export'` in `node_modules/<pkg>` | add `<pkg>` to `transformIgnorePatterns` in `jest.config.cjs` |
| `TurboModuleRegistry.getEnforcing(...): 'X' could not be found` / a native module is undefined | mock it in `setup.js`; RN `Libraries/*` modules need `{ __esModule: true, default: {...} }` |
| `No QueryClient set`, missing theme/store/i18n/safe-area context | wrap in the app's real provider in `wrap.js` (`exports.wrap`) |
| a hook needs `navigation` / `route` / data props | return them from `exports.props(screen)` in `wrap.js` |
| `requireNativeComponent` view | renders as a host view named after it: fine, codegen reports it as a gap |

Mock to the state you want to capture (logged in, data loaded, empty state); the capture is that state. Keep mocks
minimal and commented: they are the repo's fixture contract and are committed.

Done when `.spectra/ir/<Export>.jest.json` exists and lists the screen's real text.

## 3. Prove the loop on that screen (no screen is "converted" without this)

1. **Reference frames** (the judge's ground truth) from the running app, same screen and state:
   `.spectra/spectra capture runtime <ComponentName> --port <metro port> --device "<simulator name>"`
   (Metro running, app on the screen. Uses Metro's debugger: no app changes.)
2. **Same input?** jest and runtime captures should agree on types, styles and text (RN adds `overflow: hidden` to Text
   at runtime; nothing else should differ). A difference means a wrong mock or a missing provider: fix `wrap.js`/`setup.js`.
3. **Verify:** `.spectra/spectra verify .spectra/ir/<Name>.runtime.json --udid <confirmed simulator>`
   For a screen inside a navigator, pass the container RN gave it: `--frame x,y,w,h` = the root frame from the runtime
   capture, and `--ignore-safe-area bottom` for native-stack screens. Pass = every node within 1pt.
4. Record the result in `.spectra/profile.json` under `bootstrap` (screen, parity line, date) and tell the user.

## 4. Hand over

- Everyday conversion: the `spectra` skill. Figma frames: `spectra-figma`. Design-system components: `spectra-components`.
- Re-run this skill after dependency or iOS project changes, or when a capture starts failing.
