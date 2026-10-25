---
name: spectra
description: "Convert a React Native screen into native SwiftUI that lays out exactly like RN, with Spectra (orbit-SP1): capture (jest render or running app) -> IR -> generated SwiftUI on FlexLayout (a Swift port of RN's Yoga) -> verify against RN's own frames on a simulator. Use when the user says 'convert this screen', 'RN to native', 'generate SwiftUI for X', 'spectra', 'port this screen to Swift', or asks whether a generated screen matches RN. Run spectra-bootstrap first if `.spectra/profile.json` is missing."
---

# Spectra: RN screen -> native

The pipeline is code; your job is choosing the screen and state, reading the gaps, and not calling anything done
before `verify` passes. CLI: `.spectra/spectra` (no args lists commands). Preflight: `.spectra/spectra check capture-jest`
(or `capture-runtime`); if NOT READY, run the `spectra-bootstrap` skill.

## How it works (so you can reason about failures)

1. **Capture** -> IR (`<orbit-SP1>/spectra/ir/README.md`): tree of view/text/image/scroll, RN style keys (already resolved
   by React, so the styling library doesn't matter), text. Two front-ends, same IR:
   - `capture jest <file> <Export>`: any state via mocks, no simulator. No frames.
   - `capture runtime <Component>`: from the running app over Metro's debugger. Has Yoga's frames (the reference).
2. **gen**: one `<Name>.generated.swift`. Containers are data for `Flex` (a SwiftUI Layout running FlexCore, a
   line-by-line port of RN's Yoga, checked against RN's vendored Yoga on 20k random trees); text/images/backgrounds are
   SwiftUI views measured by SwiftUI. Unsupported styles are listed as gaps in the file header, never guessed.
3. **verify**: builds a preview app (`.spectra/preview`), runs the screen on a simulator, logs every node's frame and
   judges it against the IR's reference frames, root-relative, 1pt tolerance.

## Convert a screen

```bash
.spectra/spectra capture jest src/screens/Orders.tsx OrdersScreen        # the state you want (mocks in .spectra/jest)
.spectra/spectra capture runtime OrdersScreen --port 8081 --device "iPhone 17 Pro"   # same state, for reference frames
.spectra/spectra gen .spectra/ir/OrdersScreen.jest.json                    # -> .spectra/out/OrdersScreen.generated.swift
.spectra/spectra verify .spectra/ir/OrdersScreen.runtime.json --udid <sim>  # PARITY n/n within 1pt
```

- Jest and runtime captures of the same state must produce the same Swift (diff the generated files). If they don't,
  the mocks differ from the real app; fix the harness, not the output.
- Navigator screens: `--frame x,y,w,h` (root frame from the runtime capture) and `--ignore-safe-area bottom` for
  native-stack content. Navigation chrome (headers, tabs) is not converted yet: say so, don't fake it.
- Paste the PARITY line and any BAD rows to the user. Below 1pt everywhere = layout matches RN.

## Reading results

| Symptom | Likely cause |
|---|---|
| A whole subtree offset by a safe-area inset (e.g. 62pt) | the host wrapped the screen in `.ignoresSafeArea()`; or the ScrollView's `contentInsetAdjustmentBehavior` |
| Text nodes off by < 1pt, containers exact | text metrics or RN pixel rounding (FlexCore doesn't round to the pixel grid yet) |
| A node far off, its styles listed as a gap | an unsupported style (percentages, wrap, aspectRatio, borders): the gap list is the to-do |
| Glyphs ~1pt off inside equal frames (pixel diff) | RN vs SwiftUI baseline placement within `lineHeight`: known open gap |

## Not done yet (tell the user rather than work around silently)

Behaviour (onPress, state, data) is not generated: screens are static and need a typed interface for native code to
fill. Lists (`FlatList`) become fixed trees, not lazy lists. Images are placeholders. Navigation chrome isn't converted.
Design-system components: use `spectra-components` so generated screens use real native components instead of primitives.

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = the screen's IR captured and `verify` failing or unrun, before generating; gate = `.spectra/spectra verify` (PARITY n/n within 1pt); oracle = the source's own layout (rn-runtime, or jest for unreachable states), never edited to pass; floor = screens at PARITY (ready) / screens captured (breadth); log = `.spectra/DEVLOG.md`; next = this skill's "Not done yet" and the gap list from `verify`. Every misjudge fix ships a fixture.
