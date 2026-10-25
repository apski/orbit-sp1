# Orbit design layer: Figma → compact model → mappings → native

Status: proposal, 2026-10-24. Sources: ChatGPT thread "Design System Architecture" (RN screen → shared UI model →
component resolution → SwiftUI/Compose; Figma MCP too context-heavy; design-tool evaluation) plus its follow-up
(CLI consumes Figma output, three layers, `orbit design …`), run through the ambitious pass.

> **Scope (decided 2026-10-24):** generic Orbit for RN teams, i.e. RN → native app conversion run on any repo, with
> an installer that fits itself to that repo. **RN code and the running RN app are the primary source; Figma is
> optional.** A frozen production RN app is the immutable dev fixture. Gaps for the generic product: §8.

## 1. The ambitious frame

- **Obvious version:** an RN → IR → SwiftUI/Compose compiler with a component registry.
- **Altitude:** the lasting problem is **drift**: platforms and the design keep diverging after v1. Build the
  **judge** first and run it on every PR. Mapped subtrees are emitted deterministically. Agents write native code only
  for what no mapping covers, and the judge checks it. Compilers with hand-written adapters for everything (Lona,
  ReactXP) died in the adapter long tail.
- **Depth:**
  1. *What a top pro does unasked:* treat the code and Figma as **witnesses**, not specs. Recover layout constraints
     by perturbation: render at 3 widths × 3 Dynamic Type sizes × short/long/empty content and record what stretches,
     truncates, wraps or stays pinned.
  2. *Rawest material:* Figma node JSON cached at a fixed file version, plus computed frames from the running app
     (AX tree via simwalk/argent).
  3. *Methods:* deterministic normaliser (no LLM summaries), content hashes, refs + overrides, Orbit's budgeted packet,
     Style Dictionary for tokens, Code Connect for identity.
  4. *Outside judge:* per-element frame deltas between the Figma frame (`absoluteBoundingBox` at device width) and the
     implemented screen's AX frames, matched by mapping id. A context-token measurement against raw MCP output on the
     same frames. A held-out screen.
- **Range:** GIS **conflation** (two maps of one territory: identity matching with a confidence score, attribute
  precedence), the localization industry's **translation memory** (accepted mappings reused, with fuzzy-match scores),
  compiler **golden tests** (re-pull at the same Figma version must give a byte-identical model), and a **red team**
  agent that breaks parity with XXXL text, RTL, 400-char names and zero rows.

**Hard goal (doable):** `orbit design context <screen> --target swiftui --budget 4000` gives an agent enough to build
a held-out screen that passes the judge (≥95% of mapped elements within 1pt at 3 widths × 3 text sizes) with no hand
edits. It also uses ≤10% of the tokens that `get_design_context` spends on the same frames.

## 2. Ingestion: the CLI consumes Figma, the agent never does

Process the verbose response **before** it reaches agent context. If the agent calls Figma and compacts afterwards,
it has already paid the context cost. Orbit is the client: MCP client, REST client, or a small proxy.

Pick the transport by plan gate (**verify each against the current Figma plan before building**):

| Need | REST | MCP |
|---|---|---|
| Node tree with auto-layout (`layoutMode`, `layoutSizing*`, `itemSpacing`, padding, `layoutWrap`, min/max, `boundVariables`, `componentProperties`) | `GET /v1/files/:key/nodes?ids=…&version=…` | `get_metadata` is sparse (ids, names, types, position, size): no layout rules |
| Variables / tokens | `/variables/local` is **Enterprise-only** | `get_variable_defs` |
| Code Connect mappings | — | `get_code_connect_map` (Code Connect itself needs Organization/Enterprise) |
| Reference renders | `GET /v1/images/:key` | `get_screenshot` |
| Generated code | — | `get_design_context` (React + Tailwind by default): one input, not a schema |

Default: REST for structure and renders (versioned, cacheable). MCP only for variables and Code Connect when REST is
plan-gated. Rate limits on low seats are tight, which is another reason to cache.

## 3. Three layers

| Layer | Contents | Owner | Path |
|---|---|---|---|
| **Raw cache** | Original responses, assets, renders, source ids, file version, hashes | Importer; never hand-edited | `design/raw/<fileKey>/<version>/` (zstd JSON; gitignored, re-pullable) |
| **Normalised model** | Components (stored once), instances as ref + overrides, variants, token refs with aliases/units/themes, layout rules, asset ids | Generated; golden-tested | `design/model/` |
| **Mapping files** | Design concept ↔ code implementation | **People/agents, reviewed**; never overwritten by a pull | `design/map/{components,tokens,exceptions}/` |

Normaliser rules (structural extraction, not summarisation):
- Each shared component is stored once. Instances are `ref + overrides`.
- Keep token references, aliases, units and per-theme values, never resolved hexes alone.
- Keep fill, hug, fixed, min/max, alignment and wrap. Coordinates are evidence, not rules.
- Store assets separately and return ids and paths only.
- Expand only the requested subtree plus its dependency closure.
- `--since <version>`: return a diff, not the whole model.
- Anything unsupported stays in raw and is listed as a **gap**. Gaps are never dropped silently.
- Each rule records whether it is **explicit** (Figma or code says so), **observed** (perturbation), or
  **unresolved**. Conversion must never invent intent.

### 3.1 Yoga: layout rules you can run

Figma auto-layout is close to a flexbox subset: direction, gap, padding, hug/fill/fixed, min/max, alignment, wrap,
absolute children. Lower the model's layout rules to a **Yoga** tree (`yoga-layout`, the wasm build of the engine RN
uses) and the model becomes executable: deterministic frames at any width, with no simulator.

- **Normaliser judge.** Yoga(model) at the design width must reproduce Figma's `absoluteBoundingBox` for every node.
  A miss means extraction lost a rule. This is the strongest check that the "compact" model is complete.
- **Predicted frames for the parity judge.** Yoga predicts frames at 3 widths. The design has no frames at those
  widths, so Yoga gives the expected values there.
- **Lowering hints.** SwiftUI layout is proposal-based, not flexbox. Wherever SwiftUI disagrees with Yoga, the agent
  needs `layoutPriority`, `fixedSize` or a custom `Layout`. Each disagreement is a concrete, located gap, not a vibe.
- **Ceilings.** Yoga doesn't measure text. It takes a measure function, so feed it platform-measured text sizes
  (CoreText on iOS), because text metrics are where parity actually breaks. Figma grid auto-layout has no Yoga
  equivalent, so it is reported as a gap. Truncation and line limits are not Yoga either; record them as text rules.
- **Not the runtime.** Running Yoga inside the native app gives identical-to-RN layout, but it gives up idiomatic
  SwiftUI and Dynamic Type behaviour. Use it only behind a custom `Layout` container, and only if a pattern can't be
  expressed natively.

## 4. Mappings

The contract (illustrative):

```yaml
id: button.primary
design:
  figmaComponentKey: "<component-key>"
implementations:
  reactNative: { source: src/ui/Button.tsx#Button, props: { emphasis: primary } }
  swiftUI:
    source: DesignSystem/PrimaryButton.swift#PrimaryButton
    emit: 'PrimaryButton(title: {{label}})'   # deterministic emission for mapped instances
  compose:     { status: missing }
properties:
  label: { figma: Text, reactNative: title, swiftUI: title }
tokens:
  background: color.action.primary
  cornerRadius: radius.control
verified: { judge: pass, run: runs/2026-10-24-portfolio.json }
```

- **One registry, not a second format.** An existing component registry (inventory, token map, code backlinks) is
  already a code ↔ design registry when the repo has one. Add a `figmaComponentKey` column and the
  `verified` field to that registry. Do not start a parallel YAML tree.
- **Import Code Connect where it exists.** Extend it locally with contract, gaps and verification status.
- Keep components, tokens and screen-specific exceptions in separate files, so a regenerated model never touches a
  deliberate decision.
- Accepted agent matches are written back as mappings with provenance (who, when, and the evidence). That is the
  translation memory: the second screen reuses the first one's decisions.

## 5. Commands: a `design` subcommand on the existing Orbit CLI

```
orbit design pull <figma-url> [--version V]       # raw cache + model; prints added/changed/removed
orbit design inspect Portfolio --depth 2          # structure, refs, tokens; ids only for assets
orbit design gaps Portfolio --target swiftui      # unmapped components/props/tokens, unresolved rules
orbit design context Portfolio --target swiftui --budget 4000
orbit design verify Portfolio --target swiftui    # the judge; writes runs/*.json like `orbit verify`
```

- `context` reuses `orbit/packet.py`: mandatory sections first, stop at the budget, record `dropped` with expand
  commands. Sections: screen structure, component APIs from mappings, required tokens, unresolved mappings,
  reference-image paths. Every line cites its source (Figma node id + version, or file:line).
- Raw files register through `orbit/sources.py` (`[sources.figma] kind = "design"`). When nothing has been pulled
  the source stays `unavailable`; it is never an empty success. That slot already exists in the test fixture.
- the design tool reads `design/model/` as an optional editor/renderer adapter.

### 5.1 Deterministic by default, agents only where judgement is needed

**Rule:** a step is local, deterministic code unless it needs judgement that no rule or data can express yet. Same
inputs (Figma version + repo rev + mappings) → same outputs, byte for byte. Agents never call Figma, never read
`design/raw/`, and never produce a final artifact unchecked. They get a packet, return data or code, and a
deterministic check accepts or rejects the result.

| Step | Who | How |
|---|---|---|
| Pull, cache, hash, version pin | code | REST/MCP client, zstd |
| Normalise: components once, refs + overrides, token refs, layout rules, gaps | code | structural transform, golden-tested |
| Dependency closure, subtree expansion, diff since version | code | graph walk over the model |
| Tokens → Swift (and TS) | code | Style Dictionary / existing a tokens-from-code extractor |
| Identity: Code Connect import, component key, existing registry backlinks | code | exact joins |
| Identity: no exact join | code scores, **agent** only in the ambiguous band | structural similarity (tree shape, tokens, sizes, props). Above the threshold: auto-accept. Below: unmapped. Between: agent proposes, a person accepts |
| Layout prediction and model completeness | code | Yoga round-trip (§3.1) |
| Constraint capture by perturbation | code | simwalk matrix run, frames diffed |
| Emitting a mapped leaf component | code | each mapping carries an emit template (`swiftUI.emit`); props and tokens filled from the model |
| Emitting compositions and screens (containers + resolved leaves) | code | composition generator, §5.2 |
| Detecting repeated compositions, extracting their props | code | structural hash + anti-unification, §5.2 |
| Naming a promoted composition | **agent**, proposal only | a person accepts; then it becomes a mapping |
| Writing a **missing** component, or a subtree with an unresolved layout rule | **agent** | packet from `context`; output must compile and pass the judge |
| Semantic naming ("this is an AssetRow") | **agent**, proposal only | recorded as a mapping only after acceptance; appearance never changes because of a name |
| Figma vs code disagreement on intent | **person**, agent may draft | written to `design/map/exceptions/` with provenance |
| Judge, gaps, registry check, packet budgeting | code | simwalk + Yoga + `orbit verify` |

**Promotion (so agent work shrinks over time):** every accepted agent output is stored as data, never as one-off
code. A new match becomes a mapping. A new component becomes a mapping plus an emit template. A repeated layout fix
becomes a normaliser or lowering rule with a golden test. The next run consumes it deterministically. A second
agent call for a question that was already accepted counts as a bug.

### 5.2 Composition generator

Emit templates cover leaves (a `PrimaryButton`). Most of a screen is **compositions**: containers that arrange
resolved leaves (icon + two labels + trailing amount). Without a generator, every container would go to an agent.
**A screen is the root composition, so one generator handles both.** Build order: tokens → leaf components →
compositions → screen → preview fixtures.

**Input:** a model subtree whose leaves are all resolved (a mapped component, or a primitive `Text`/`Image`/`View`),
with containers carrying layout rules (§3). An unresolved leaf blocks only its own subtree; the rest still emits.

**Container lowering, deterministic, in order:**
1. **Pattern table.** Flex arrangements with a proven SwiftUI equivalent. Example: a row, no wrap, children hug
   except one fill → `HStack(spacing:)` + `.frame(maxWidth: .infinity)` on the fill child. A column with gap →
   `VStack(spacing:)`. Absolute children → `ZStack`/`.overlay` with offsets. Each row has a golden test, and Yoga vs
   SwiftUI frames must agree in the preview host across the width × text-size matrix.
2. **Fallback: `FlexLayout`.** One Swift `Layout` implementing the flex subset the model uses (direction, gap,
   padding, grow/shrink/basis, align, justify, wrap, min/max). It is tested against Yoga on generated cases, so any
   flex container emits correctly even without a pattern row. Patterns are an idiom upgrade, not a requirement.
3. **Agent** only when neither applies (grid, transforms, a text-measurement quirk the judge catches). The fix is
   promoted into a pattern row.

**Repeated compositions (translation memory, made deterministic):**
- **Structural hash** per subtree: shape + component refs + token refs + layout rules, ignoring content. The same
  hash across ≥3 instances (or ≥2 screens) makes it a promotion candidate.
- **Anti-unification** (Plotkin; the method clone-refactoring tools use) over the instances gives the template plus
  holes. The holes are exactly what varies between instances (text, image, a token choice), and they become the
  composition's props. Values that never vary stay constants.
- The extremes seen across instances (longest label, empty slot) become that composition's preview fixtures.
- Promotion writes `design/map/components/<id>.yaml` with `origin: composition` and an emit template that calls the
  generated view. Below the threshold, the composition stays screen-local, inlined in the screen's generated file.

**Output:** one `<Name>.generated.swift` per promoted composition and one per screen. Props come from anti-unification,
tokens from the token pipeline, and `accessibilityIdentifier` = model id on every node so the judge can match it.
There is a gallery entry in the preview host with the fixture variants. The ownership rules in §8.6 apply.

**Pattern table status (2026-10-25):** built as `backends/stacks.mjs` (one plan) with two emitters,
`backends/swiftui/lower.mjs` (VStack/HStack + frames) and `backends/uikit/lower.mjs` (UIStackView + Auto Layout).
`spectra verify --backend swiftui|uikit` runs them. All three fixtures (Fixture A (production RN app Home), Fixture B (NavCore Detail), Figma Checkout)
match RN within 0.33pt on both back-ends in Release, with zero ambiguous UIKit layouts. FlexLayout stays as the `pixel`
option and the fallback for nodes reported as unlowered. Rules and learnings: `skills/spectra-components/SKILL.md` §5.

**FlexLayout status (2026-10-24):** the core is ported, `agents/orbit/design/flexlayout/`. It is a Float32 port of
Yoga as vendored in RN 0.76.5, including the measurement cache, whose reuse rules change real layouts. It matches
`yoga-layout@3.2.1` (RN config) on **20000/20000** random trees from a held-out seed, and 6 deliberate mutants are
each caught. Not yet: margins, wrap, absolute, percentages, aspectRatio, pixel rounding, and the SwiftUI wrapper.

**A constraint the wrapper must respect:** a SwiftUI proposal carries a size but not Yoga's sizing mode (exact vs
at-most vs unbounded). So nested containers can't each be their own `Layout`, or the mode is lost at every level and
the layout diverges from Yoga. Instead, emit **one `Flex` per composition** whose container tree is data (a spec of
node ids + styles), with only the leaves (text, images, mapped components) as subviews measured by `sizeThatFits`.
Container decorations (background, border) are subviews placed at their node's frame. Promoted compositions must
splice their spec into the parent tree rather than nest as opaque views.

**Judge per composition** as well as per screen. A composition that fails is located before the screen-level diff
mixes its error with others.

## 6. Verification (nothing grades itself)

| Check | Pass condition |
|---|---|
| Normaliser golden | Re-pull at the same file version gives a byte-identical `design/model/`; the digest is in the test |
| Context efficiency | Tokens for `context --budget 4000` vs raw `get_design_context` on the same 3 frames, plus the judge result for an agent built only from each. The reduction counts only if the judge result holds |
| Parity judge | simwalk flow `flows/parity-<screen>.mjs` reads the native AX frames by `accessibilityIdentifier` = mapping id and compares them to Figma frames: ≤1pt, at 3 widths × 3 Dynamic Type sizes |
| Held-out | Tune on screen A; build screen B from `context` alone; judge B blind |
| Agent share | Each run logs agent calls per screen by kind (match / author / naming), next to emitted vs agent-written lines. Screen B must need fewer agent calls than A, and only for components that A did not have |
| Determinism | Two runs on the same inputs give identical `design/model/`, emitted SwiftUI and packets (digest compare) |
| the design tool fidelity | `design-tool fig convert` on one screen, then change one token and one layout rule in Figma and re-import. Count preserved instances/variants/token bindings/constraints. the design tool becomes central only if this round-trip holds |

## 7. First slice

1. One Figma frame that has a the native rewrite SwiftUI counterpart, plus its component closure. `pull` → raw + model, with the
   golden test and the Yoga round-trip (Yoga(model) = Figma frames at the design width).
2. Run `gaps` and fill the mappings for that closure, starting from the existing the design tool registry.
3. Add `accessibilityIdentifier`s and write the parity flow → judge table on the current implementation (baseline).
4. Measure `context` vs `get_design_context` tokens on the same frames.
5. Run the held-out screen B. Only after it passes: the design tool fidelity test, then the red team fixtures.

Skipped on purpose:
- A general IR-to-code compiler and full platform adapters. Emission is per-mapping templates; unmapped work goes to
  agents until it repeats often enough to become a template.
- RN extraction (retired here, see the conflict at the top).
- Compose (not a goal).

## 8. What's missing for a generic RN → native product (ranked)

Sections 2–7 are Figma-first. For RN teams the RN app is the source, many teams have no Figma (or a stale one), and
the tool has to fit repos it has never seen. Ranked by what blocks the first conversion:

1. **RN extraction, static + runtime.** Static (ts-morph): screen → component tree, imports, props, `file:line`
   provenance. Runtime (running app via the debugger): **host-level resolved styles and Yoga layout metrics** per
   node. After resolution, every styling library (StyleSheet, NativeWind, styled-components, Restyle, Tamagui,
   Unistyles) is just `View`/`Text` style props, so one runtime capture replaces N per-library parsers. RN's own Yoga
   tree *is* the layout model, which makes the §3.1 round-trip exact on the RN side. Static code supplies structure
   and provenance; runtime supplies values.
2. **The installer is a repo profile, not a third bootstrap.** NavLab already has `arch/profile.py detect|check`
   ("one install, one repo profile", evidence per field, the human confirms once, each skill asks only for what's
   missing). Orbit has `bootstrap/census.py` + `catalog.toml` signals. Merge them into one profile. It detects: Expo
   or bare, RN version, new arch, navigation lib, styling lib, DS roots, icons/fonts, theming/dark mode, i18n,
   testID convention, data/state libs, existing native targets (SwiftUI/UIKit/Compose, min OS), native DS, and
   build/run commands. **Install is done only when a smoke test passes:** extract one screen, render it on both
   sides, and run the judge.
3. **Same data on both sides.** A screen renders nothing useful without state, and the judge means nothing if RN and
   native show different data. This needs scenario capture (record API responses from the running RN app, keyed by
   endpoint, via `contracts-scan`'s endpoint inventory), replay into both apps, an auth bypass, and a deep link per
   screen + state.
4. **Native preview host.** A generated target that renders any screen + fixture by deep link without booting the
   full app. Plus a greenfield scaffold when the repo has no native project yet.
5. **Lowering tables: this is where determinism lives.** RN style prop → SwiftUI (later Compose), one row each with
   a golden test: `lineHeight`, `letterSpacing` → tracking, `fontWeight` → font file, `allowFontScaling` /
   `maxFontSizeMultiplier` → Dynamic Type limits, shadow/elevation, per-side borders, `hairlineWidth`, percentages,
   `aspectRatio`, absolute positioning, `flexShrink` vs `layoutPriority`, and accessibility props → traits. Assets:
   vector-icon glyph → SF Symbol map or exported glyph, images, Lottie.
6. **Generated vs hand-owned native code.** Regenerating must never clobber an edit. Use separate
   `*.generated.swift` files, extension points for hand code, and an `ejected` status on the mapping once a person
   owns a file. This is missing from the plan entirely.
7. **Beyond the view.** Navigation (routes, params, deep links), data/state, analytics, flags, storage keys, native
   modules, IAP, push. A view-only converter leaves most of the work. Minimum: `contracts-scan` inventory per screen,
   a generated typed view interface per screen (data in, actions out) to port behaviour against, and the hybrid path
   so conversion goes screen by screen. NavCore (2026-10-22) showed a native stack hosting unmodified React
   Navigation flows.
8. **Fidelity policy per component:** `pixel` (match RN) or `idiomatic` (native nav bar, `List`, sheet), with judge
   tolerance per policy. Without it, a correct idiomatic choice reads as a failure.
9. **Generality benchmark: the outside judge for "generic".** Measure install + extract + judge success on repos
   never tuned on, with zero hand edits. Dev fixture: a frozen production RN app (immutable commit) with its hand-written SwiftUI rewrite as the native reference. Held out: 3 open-source RN apps (e.g. bluesky `social-app`, `mattermost-mobile`, `rainbow`), run
   only for scoring.
10. **Android.** Compose lowering, plus an Android judge. simwalk is iOS-only; argent drives Android through
    uiautomator. `android-parity` already has the NavLab-side twin pattern.
11. **Runtime and packaging.** Orbit is Python; ts-morph, yoga-layout and simwalk are Node. Run the RN extractor as a
    Node L1 adapter that emits JSON facts into Orbit's index; that fits SPEC §3 layers without porting either side.
    Distribution (npx / pip / brew) is still undecided. Everything stays local; no code leaves the machine.
12. **Interaction and motion** (Pressable states, Reanimated, gesture handler): reported as gaps in v1, not converted.

**Revised first slice (generic):** profile + smoke test on the frozen RN fixture → runtime capture of one screen
→ lowering tables for the props that screen uses → generated SwiftUI in a preview host with replayed data → judge.
Then run the install on one held-out repo cold, and count every hand edit it needed: each one is an installer or
lowering gap.
