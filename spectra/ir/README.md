# Spectra UI IR (v1)

One JSON document per screen (or component). Front-ends emit it, the back-end reads it, and the judge compares
against its reference frames. Code: `ir.mjs` (`normalize`, `validate`).

```jsonc
{
  "ir": 1,
  "screen": "Detail",                       // component / frame name
  "source": "rn-jest" | "rn-runtime" | "figma",
  "container": [402, 652],                  // optional: size the source laid the screen out in
  "roots": [ /* nodes */ ]
}
```

Node:

| Field | Meaning |
|---|---|
| `type` | `view` · `text` · `image` · `scroll` · `scroll-content` · `safe-area` |
| `style` | RN style keys. Layout keys are Yoga's (`flexDirection`, `padding*`, `margin*`, `gap`, `position`, `top`…); paint keys are RN's (`backgroundColor`, `borderRadius`, `opacity`, `color`, `fontSize`, `fontWeight`, `lineHeight`, `textAlign`, `letterSpacing`, `fontFamily`) |
| `text` | Text content (text nodes are leaves) |
| `frame` | Optional reference frame `[x, y, w, h]` in points, window/frame space: what the judge compares against (RN runtime: Yoga's result; Figma: `absoluteBoundingBox`; jest: none) |
| `component` | Optional component boundary `{ name, props, key? }` (RN composite or Figma instance): what the registry maps to a native component |
| `testID`, `numberOfLines`, `horizontal`, `contentInsetAdjustmentBehavior`, `source` | As in RN |
| `hostType` | Original host type when it had no IR equivalent (reported as a gap by codegen) |

Why Yoga's vocabulary: the Swift runtime (`backends/swiftui/FlexLayout`) is a line-by-line port of the Yoga RN ships, so
anything lowered into these keys lays out natively exactly as RN would. A front-end whose source isn't flexbox (Figma
auto layout) owns the lowering and its verification; see `frontends/figma/lower.mjs`.
