# simwalk

Scripted, LLM-free walks through iOS-simulator user journeys. A journey is a short JS file of steps like
`tap({ id: 'About' }, { until: 'iOS Version' })`. It replays at roughly UI-animation speed (~0.5 s per
navigation step), with nothing sent to a model unless a step breaks.

simwalk reuses the two native binaries shipped by [argent](https://github.com/software-mansion/argent)
and talks to them directly, skipping argent's MCP server, tool-server, auto-screenshots and the LLM
round-trip per step.

```bash
npm i -g @swmansion/argent     # provides the native binaries; simwalk finds them via `which argent`
node simwalk.mjs flows/settings-about.mjs --repeat 3   # uses the first booted simulator, or --udid <UDID>
```

```
settings-about  run 1/3
    3448ms  launch Settings at root
     524ms  open General
     563ms  open About
     470ms  back to General
    1018ms  back to Settings
    6024ms  TOTAL
```

Writing journeys: see **[FLOWS.md](FLOWS.md)**.

---

## How argent works (reverse-engineered)

The JS/TS layers come from argent's public source. The two native binaries were inspected with
`otool`/`strings` and the REA MCP (`inspect_macho`).

```
LLM ──MCP──▶ mcp-server.mjs ──HTTP──▶ tool-server.cjs ──┬─WS JSON──────▶ simulator-server  (host, Rust, closed)
                                                        │                 SimulatorKit SimDeviceLegacyClient → Indigo HID touches
                                                        │                 IOSurface framebuffer → screenshots
                                                        └─unix NDJSON◀── ax-service         (runs INSIDE the sim via `simctl spawn`)
                                                                          dlopen AXRuntime → AXElement.systemApplication / primaryApp /
                                                                          explorerElements, AXSpringBoardServer.isSystemAppShowingAnAlert
```

- **The screen is read from the accessibility tree, not from pixels.** `ax-service` is a ~140 KB iOS-simulator
  binary (Mach-O platform 7). It walks the foreground app's AX elements and returns label, identifier, traits,
  value, a normalized `[0,1]` frame and a tapPoint. For that to work, argent sets `AutomationEnabled` and
  `IgnoreAXServerEntitlements` in the sim's `com.apple.Accessibility` prefs before boot.
- **Touches** are WebSocket commands `{cmd:"touch", type:"Down"|"Move"|"Up", x, y}` (normalized coords) sent to
  `simulator-server`, which injects them as Indigo HID events through SimulatorKit.
- **How the LLM is used:** every interaction tool (tap, swipe, type…) returns, in the same MCP result:
  1. a wait until the tree stops changing (`await-screen-idle`: 200 ms polls, 250 ms stable → ≥350 ms);
  2. a screenshot (25% scale, ≈300×650 px);
  3. the full AX tree as text.

  The model reads that result and chooses the next tap's coordinates. Every step is a full LLM turn, and every
  result stays in context.

### What argent puts in context, per action (Settings root screen)

| | argent (MCP) | simwalk |
|---|---|---|
| AX tree text | 2,634 chars (~700 tokens) | none |
| screenshot | ~300×650 px image (~260 tokens) | none (saved to disk on failure only) |
| per step total | **~1k tokens, accumulating** (30 steps ≈ 30k) | **0** |
| on failure | — | outline `id \| label \| value`, 961 chars (~250 tokens), sent once |

## Benchmarks

iPhone 17 Pro sim, iOS 26.4, Apple silicon, argent 0.25.0.

| primitive | argent tool-server | simwalk direct |
|---|---|---|
| read AX tree | 65 ms | 58 ms |
| tap (down+hold+up) | 57 ms | ~52 ms (50 ms is hold) |
| JS parse + match of the tree | — | 0.05 ms |
| HID command ack | — | 0.19 ms |
| per-step overhead the MCP layer adds (idle wait + screenshot + tree) | ~480 ms | 0 |

**Takeaways**

- argent's native layer is already fast. The ~58 ms tree read happens inside the simulator, so the language is
  irrelevant: rewriting the driver in Swift or Rust would save under 1%.
- A navigation step is bound by the UIKit push/pop animation. The new screen appears in the tree ~200 ms after
  the touch and settles ~120 ms later (`bench/step-timeline.mjs`).
- The real cost of an agentic run is the LLM turn per step (seconds) and the ~1k tokens of context per step.
  Script the journey, and bring the model in only when a step breaks.

## Bugs found in argent 0.25.0

1. **Stale simulator-server after a sim reboot.** A `simulator-server` started before a shutdown/boot kept
   serving the old framebuffer: the screenshot showed the clock and app from two days earlier, and touches
   didn't land. Fix: `stop-simulator-server` for that device; argent respawns it.
2. **ax-service aborts on NaN frames mid-transition.** `NSJSONSerialization` throws `Invalid number value (NaN)`
   during some push/pop animations. The daemon dies, and a caller without a timeout hangs. simwalk times out
   every query and respawns the daemon (~0.5 s). Newer argent versions may have fixed this.

## Driver API (`driver.mjs`)

```js
import { connect } from './driver.mjs';
const d = await connect(udid);
d.launch(bundleId)                  // simctl launch --terminate-running-process
await d.find(sel)                   // → { el, d } | undefined
await d.waitFor(sel, timeoutMs)     // tight-polls the AX tree
await d.tap(sel, { until, timeoutMs })  // stable-frame tap, confirmed by `until`, auto re-tap
await d.tapXY(x, y)                 // normalized coords
await d.swipe(x1, y1, x2, y2, ms)
await d.outline()                   // compact "id | label | value" list
d.screenshot(file)                  // live framebuffer via simctl
d.close()
```

Selectors: `'text'` (case-insensitive substring of label or id), `{ id }` (exact accessibilityIdentifier),
`{ label }` (exact label).

## Not done yet

- Text input (argent's `keyboard`/`fill` paths aren't wired up); `simctl pbcopy` + paste works meanwhile.
- Android (argent drives Android through its own APK and adb; not ported).
- Automatic flow repair by an agent: today a failure prints the outline and a screenshot, and you (or an
  agent) edit the flow.

## Repo

- `driver.mjs` — the driver (~150 lines, zero deps, Node ≥ 22)
- `simwalk.mjs` — the runner: per-step timing; on failure, outline + screenshot
- `flows/` — example journeys
- `bench/` — `argent-primitives.mjs` (argent tool-server timings), `overhead.mjs` (JS vs native),
  `step-timeline.mjs` (where a step's time goes)

MIT. argent is Apache-2.0 by Software Mansion; simwalk doesn't redistribute its binaries.
