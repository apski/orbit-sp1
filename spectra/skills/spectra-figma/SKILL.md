---
name: spectra-figma
description: "Figma frame -> native SwiftUI with Spectra (orbit-SP1), through the Figma REST API instead of the Figma MCP: one pull into a local cache, a compact outline for reading, deterministic lowering of auto layout into Yoga, generated SwiftUI, and a judge against Figma's own boxes. Use when the user shares a figma.com link, says 'figma to swift', 'build this design natively', 'implement this frame', 'spectra figma', or wants design tokens/components from Figma. Saves tokens: never load raw Figma JSON or Dev Mode MCP output into context."
---

# Spectra: Figma -> native

Rule: **the CLI reads Figma, you read the outline.** Raw node JSON is far bigger than the outline (fixture: 19 KB raw vs 2 KB outline; real files carry more fields per node);
the pull is cached (zstd) under `.spectra/figma/raw/` and every later step reads the cache with no API calls.

Preflight: `.spectra/spectra check figma`. The token is `FIGMA_TOKEN` (env var) or a `FIGMA_TOKEN=...` line in
`.spectra/secrets.env` (gitignored) or `~/.spectra/secrets.env`. Ask the user to put it there; never ask them to paste it.

## Steps

```bash
.spectra/spectra figma pull '<figma link with ?node-id=...>'     # raw subtree + components + Figma's @3x PNG render
.spectra/spectra figma inspect '<link>' --depth 3                # one line per node: what you read and reason about
.spectra/spectra figma lower '<link>'                            # -> .spectra/ir/<Frame>.figma.json + gap list
.spectra/spectra verify .spectra/ir/<Frame>.figma.json --udid <sim>   # SwiftUI vs Figma's boxes, PARITY n/n
```

- Link: select the frame in Figma, Copy link to selection (it carries `node-id`). Branch links work.
- `verify` places the screen in a container the frame's size, so Figma boxes and native frames compare 1:1.
- Pixel check: compare the simulator screenshot against the pulled `@3x.png` (Figma's own render).

## How auto layout is lowered (frontends/figma/lower.mjs)

| Figma | Yoga / IR |
|---|---|
| layoutMode HORIZONTAL / VERTICAL | flexDirection row / column |
| itemSpacing, padding | gap, padding (SPACE_BETWEEN ignores itemSpacing, as Figma does; negative spacing -> margins) |
| primary / counter axis align | justifyContent / alignItems (Figma never stretches by default) |
| child FIXED / HUG / FILL | size set / unset / main axis `flexGrow 1, flexBasis 0`, cross axis `alignSelf stretch` |
| ABSOLUTE child, or a frame/group without auto layout | `position: absolute` + insets from constraints |
| strokes | outline (drawn outside layout, like Figma), INSIDE/CENTER/OUTSIDE offsets |
| drop shadow | shadow, radius = blur / 2 |
| instance | `component: { name, key, props }` (variant + text props) for the component registry |

Everything that doesn't lower exactly is printed as a gap (grid layout, CENTER/SCALE constraints, mixed text styles,
gradients, image fills, per-side strokes, strokes included in layout, rotation). Report gaps to the user; don't
hand-patch generated files.

## Things to know

- Fonts: Figma text in a non-system family (Inter, brand fonts) generates `.custom(<PostScript name>)`; the app must
  bundle the font or iOS silently falls back. `detect` lists fonts found in the repo.
- Text boxes come from Figma's own text engine; iOS text can differ by a fraction of a point. Frames are compared
  within 1pt; for a pixel-exact check use the PNG.
- iPhone frames often include a status bar / home indicator as instances: they are system chrome, not content. Not yet
  mapped automatically: tell the user and exclude them from conclusions.
- Variables (design tokens) via REST need an Enterprise plan; Code Connect needs Organization+. Verify on the user's
  plan before promising either.
