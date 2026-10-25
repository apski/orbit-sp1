# Spectra

A UI compiler: React Native screens (jest render or running app) and Figma frames (REST API) become one IR, the IR
becomes SwiftUI laid out by FlexLayout (a line-by-line Swift port of the Yoga RN ships), and a judge checks every node
against the source's own layout. No model in the pipeline; agents choose screens/states, write per-repo mocks, and
read gaps. Plan and history: `docs/design-layer.md`.

```
frontends/rn-jest      react-test-renderer under the app's jest preset -> IR (any state, no simulator)
frontends/rn-runtime   running app over Metro's debugger -> IR + Yoga frames (the reference)
frontends/figma        REST pull (zstd cache) -> inspect (agent view) / lower (auto layout -> Yoga) -> IR + Figma boxes
ir/                    IR v1: view/text/image/scroll..., RN/Yoga style keys, optional reference frames, components
backends/swiftui       codegen.mjs (IR -> <Name>.generated.swift) + FlexLayout (FlexCore Yoga port + Flex Layout)
verify/judge.mjs       reference frames vs logged native frames, root-relative, 1pt
preview/               host app template; `spectra verify` builds it per repo in .spectra/preview
bootstrap/             detect (profile), install, jest harness templates
skills/                spectra-bootstrap (L1), spectra, spectra-figma, spectra-components
bin/spectra.mjs        the CLI (`node bin/spectra.mjs` lists commands)
```

## Verified results (2026-10-25)

| Check | Result |
|---|---|
| FlexCore vs RN 0.76.5's vendored Yoga C++, 20k held-out random trees | 19750/20000 (19825 vs a no-FMA build: 75 are clang FMA rounding) |
| Fixture A: a production RN app's Home (runtime capture), Debug + Release | 13/13 nodes within 1pt, worst 0.33pt (RN pixel rounding) |
| NavCore Detail (RN 0.88): jest capture vs runtime capture | identical generated Swift; 11/11 within 1pt, worst 0.17pt |
| Figma fixture with hand-computed boxes (fill/hug/fixed, space-between, absolute, stroke, shadow, instances) | 21/21 exact |
| Fresh repo: `install` -> bootstrap (2 mocks/providers) -> jest capture | same Swift as the device capture |
| `node --test tests/*.test.mjs` | 10/10 (Figma lowering, URL parsing, cache) |

## Not done yet

- Real Figma files: the REST client and lowering are untested against a live file (no token on the dev machine).
  First run on a real frame: `spectra figma pull` -> `inspect` -> `lower` -> `verify`, and compare the PNG render.
- Behaviour (events, state, data), navigation chrome, lists as lazy lists, images/vector assets (placeholders), custom
  fonts bundling, text baseline placement inside `lineHeight` (~1pt), pixel-grid rounding, percentages, wrap,
  aspectRatio, borders, grid auto layout, Figma system chrome (status bar instances).
- iOS targets below 26: generated text uses `.lineHeight`; needs a fallback.
- FlexCore: 0.9% of random trees with absolute positioning plus conflicting constraints still differ from Yoga.
