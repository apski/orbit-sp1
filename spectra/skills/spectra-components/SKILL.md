---
name: spectra-components
description: "Turn a React Native app's design-system components into a verified native (SwiftUI/UIKit) component library, bottom-up: inventory the RN DS, capture every component variant into Spectra IR, emit a pixel-exact FlexCore version as the baseline, sweep screens for repeated compositions (structural hash + anti-unification) to find the reusable components, lower them to idiomatic native stacks, and promote each verified component into the registry that orbit-SP1 reads. Agents only name, add behaviour/accessibility and fix unlowered subtrees; the judge checks everything. Use when the user says 'port the design system', 'native components from RN', 'build the component library', 'atoms and compositions', 'reusable components sweep', 'flexcore version of the DS', or picks a repo to convert with Spectra."
metadata:
  home: "$(.orbit/home)/spectra in an installed repo (the orbit-SP1 checkout: $ORBIT_HOME)"
  plan: spectra/docs/design-layer.md §4 (registry), §5.1 (who does what), §5.2 (composition generator)
  before: spectra-bootstrap (repo profile, jest harness, preview app)
  siblings: spectra (one screen: capture → gen → verify), spectra-figma (Figma as the source)
  related: ds-audit (inventory), rn-native-migration (whether to port at all)
---

# Spectra components: RN design system → native library

The outcome is **a native component library where every component is proven against its RN original**, recorded so
the next screen uses it by rule rather than by agent. Build order is fixed: tokens → atoms → compositions → screens.
A layer only starts once the layer below passes the judge.

Rule (design-layer §5.1): every step is deterministic code unless it needs judgement. An agent gets a budgeted packet,
returns data or code, and the judge accepts or rejects. Accepted agent output is promoted to data, so asking an agent
the same question twice is a bug.

```bash
.spectra/spectra check     # profile from spectra-bootstrap: DS roots, jest harness, preview app, simulator
orbit status               # orbit-SP1's Orbit engine sees the repo
```

## 1. Inventory (code)
`ds-audit` lists components with uses, prop variants and competing implementations:
```bash
node $NAVLAB_HOME/arch/facts.mjs . && python3 $NAVLAB_HOME/arch/ds.py .navlab/facts.json --json .navlab/ds.json
```
Resolve competing implementations first (two Buttons → pick the target). Porting both doubles the native tax.
Classify: an **atom** renders only host primitives (View/Text/Image/Pressable). A **composition** renders other DS
components. Order the work topologically over the DS import graph, atoms first.

## 2. Capture every variant (code)
Variants are the prop combinations call sites actually pass (from `ds.json`), plus extremes: longest label, empty
slot, disabled. For each one:
- **Structure:** `.spectra/spectra capture jest <file> <Export>` → `.spectra/ir/components/<Name>/<variant>.jest.json`. Any
  state, no simulator.
- **Reference frames:** jest has none. Render all variants on one gallery screen (an RN Storybook story if the repo
  has one, else a dev-only `SpectraGallery` screen), then `.spectra/spectra capture runtime SpectraGallery`. Without frames a
  component can be generated but not judged, so it can't be promoted.

## 3. FlexCore baseline (code)
`.spectra/spectra verify <ir.json>` runs gen → preview → simulator → judge for each variant. FlexCore is RN's Yoga ported to
Swift, so a failure here means the capture is incomplete (a missing style key, font, image), not a layout difference.
Fix the capture until every variant passes at ±1pt. This baseline is also the `pixel` fidelity option for components
that must match RN exactly.

## 4. Sweep for compositions (code)
Run over every screen IR, not just the DS folder. Ad-hoc compositions (icon + two labels + amount) are where the
reusable components nobody named are hiding.
- **Structural hash** per subtree: shape, component refs, token refs, layout keys, ignoring text and image content.
- A hash with ≥3 instances, or instances on ≥2 screens, is a candidate.
- **Anti-unification** over the instances gives a template with holes. Holes (what varies) become props. Values that
  never vary stay constants. The extremes across instances become preview fixtures.
- Subtrees that match an existing DS component's capture resolve to a call to that component, not a re-emitted tree.

## 5. Idiomatic native (code, then agent for the rest)
```bash
.spectra/spectra verify <ir.json> --backend swiftui     # VStack/HStack + frames, no FlexLayout
.spectra/spectra verify <ir.json> --backend uikit       # UIStackView + Auto Layout
node $(.orbit/home)/spectra/backends/swiftui/lower.mjs <ir.json> Out.swift --no-probes   # production view, no judge hooks
```
One plan (`backends/stacks.mjs`) feeds both emitters. Per node it decides stretch/grow, justify (spacers or frame
alignment), absolute (overlay or pinned subview), equal split (`flex: 1` siblings) and pass-through wrappers. The
`coverage` line lists **unlowered** nodes: those fall back to FlexCore or go to the agent. Judge again:
- At the default text size: frames within ±1pt of RN.
- At accessibility text sizes: structural checks only (no truncation, no overlap, every control reachable). Not built yet.
- UIKit only: the log must show `ORBIT_AMBIGUOUS 0` (`hasAmbiguousLayout` across the tree).

Measured 2026-10-25, Release, iPhone 17 Pro sim:

| Fixture (nodes) | FlexCore | SwiftUI stacks | UIKit stacks |
|---|---|---|---|
| RN Home screen (13) | 13/13, 0.33pt | 13/13, 0.33pt | 13/13, 0.33pt |
| RN Detail screen (11, 6 wrappers collapsed) | 11/11, 0.17pt | 11/11, 0.17pt | 11/11, 0.00pt |
| Figma Checkout (21, absolute + equal split) | 21/21, 0.00pt | 21/21, 0.00pt | 21/21, 0.17pt |

So FlexCore isn't needed in production for flex that stacks can express. Keep it as the `pixel` option and the
fallback for unlowered nodes. Rule decisions are pinned by `node --test tests/stacks.test.mjs`.

### Lowering learnings (each one cost a failed judge run)
- **Safe area, SwiftUI:** `.ignoresSafeArea()` zeroes the insets for everything inside it, so a ScrollView under an
  RN-style full-window root loses its automatic top inset (62pt off). Read the insets with a `GeometryReader`
  outside the ignore and re-apply them with `.contentMargins(edge, inset, for: .scrollContent)`, only on the edges the
  scroll view overlaps.
- **Safe area, UIKit:** UIStackView's `insetsLayoutMarginsFromSafeArea` defaults to true, which adds the safe area to
  RN padding (34pt bottom). Set it to false on every stack that has margins.
- **UIKit `.automatic` only adjusts axes that can scroll.** Short content gets no inset unless
  `alwaysBounceVertical = true`. That is RN's default, so emit it.
- **Representables under `.ignoresSafeArea()` see zero insets**, so the preview hosts UIKit through a child view
  controller and sets `additionalSafeAreaInsets` to the window overlap. Report frames after layout settles, not
  from the first `layoutSubviews`.
- **Yoga semantics to map, not copy:**
  - Explicit `width` includes padding, so SwiftUI gets `frame(width: w - padding)` inside `.padding`.
  - Stretch inside a hugging parent means "as wide as the widest sibling": SwiftUI `.fixedSize` on the parent; UIKit
    gets it free from `.fill`.
  - `grow` does nothing when the parent's main size is undefined.
  - Absolute insets measure from the parent's border box, so the overlay goes after padding and background.
- **A UIStackView aligns all children alike.** A lone `alignSelf: stretch` child gets a width constraint to the
  stack minus margins. Other `alignSelf` overrides are unlowered.
- **Cross-axis margins:** UIKit merges a lone child's margins into the parent's layout margins, otherwise it wraps the
  child in a margin view. SwiftUI uses `.padding` after the probe.

## 6. Agent: judgement only
The packet comes from orbit-SP1: `orbit packet <unit> "<task>" --budget 4000`, plus the component's IR, variants
and judge table. It covers, per component:
- The semantic role (nested views around blue text → `Button`), with accessibility label, traits and grouping.
- A typed interface: data in, events out (`onTap`). The generated view stays stateless.
- A name proposal for a promoted composition. A person accepts it; appearance never changes because of a name.
- Unlowered subtrees.

The output must compile and pass the step 5 judge. A fix that generalises becomes a lowering rule with a golden test.

## 7. Promote (code)
Each passing component goes into `.spectra/map/components/<id>.yaml` (format: design-layer §4). An entry records:
- RN source and Swift source, plus the emit template.
- The props map, from anti-unification holes and the typed interface.
- Tokens.
- `fidelity: pixel | idiomatic`, and `origin: ds | composition`.
- `verified:` with the judge run path.

Screens generated afterwards emit `PrimaryButton(title:)` instead of re-lowering the tree.

## orbit-SP1 wiring (`orbit.toml` in the target repo)
```toml
[units.rn-ds-component]          # DS roots from the spectra profile
[sources.spectra-map]
kind = "design"
glob = ".spectra/map/**/*.yaml"
[checks.spectra-parity]          # re-judges every promoted component; required once the library exists
command = [".spectra/spectra", "verify", "--all-mapped"]
required = true
```
Judge runs land in `runs/` like any `orbit verify` check, so a regression in a promoted component fails CI.

## Status (2026-10-25): be honest about what runs
| Step | State |
|---|---|
| jest + runtime capture, FlexCore gen, judge, verify, preview | built |
| `stacks.mjs` + SwiftUI/UIKit lowering, `verify --backend` | built: 3 fixtures pass on all 3 back-ends. Not yet: wrap, percentages, aspectRatio, unequal grow ratios, Dynamic Type matrix |
| Component boundaries in captures | **gap**: `jest-capture.js` emits host nodes only, so composites (`Button`) are lost. Read them from `r.root` (test instances) |
| Gallery screen generator, structural hash + anti-unification sweep, registry writer, `--all-mapped`, orbit.toml wiring | not built; do these by hand and log each one as a gap |

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill spectra-components --kind <missing-tool|missing-pattern|wrong-answer|...> --impact <blocked|wrong-answer|degraded|slow> --repo <name> --what "..." --evidence <file:line> --workaround "..."`.

## Harness (long task)
Runs under orbit-SP1's `HARNESS.md` (`$(.orbit/home)/HARNESS.md`): acceptance test first, a floor that only rises (measured vs estimated, ready vs breadth), a log entry and a green gate before you stop, edit only files you own, and ask a human only about licences, spending, publishing, personal data, destructive git or anything unclear.
Here: acceptance = each variant captured and judged before the layer above starts; gate = `.spectra/spectra verify` per variant (±1pt; UIKit also `ORBIT_AMBIGUOUS 0`); oracle = the captured RN layout; floor = variants at PARITY (ready) / variants captured (breadth), per layer; log = `.spectra/DEVLOG.md`; next = the lowest layer with a failing variant.
