# Prior results: RN navigation scaling (rn-vs-native, 2026-10-24)

Expo 58 / RN 0.88 / React Navigation 7 (native-stack + JS bottom-tabs), Fabric + Hermes, Release,
iPhone 17 Pro simulator on iOS 26.4, 3 reps, RN and the UIKit twin interleaved. render = push() → views attached + committed.
These are simulator timings with host noise, so the RN/native ratios are the robust numbers.

| Knob | RN starts costing at | Mechanism | Native |
|---|---|---|---|
| depth × tab routes | d8 × 300: render 185ms (2.8×), JS 132ms/push, push 664ms, 312MB | every ancestor navigator re-renders on each push, and every tab navigator re-renders its tab bar | flat 68ms |
| nesting depth alone | ~12 levels, +17ms (1 frame); depth 3–5 free | ~1.5ms JS per ancestor navigator per action | +10% |
| registered stack screens | never (3→300 flat) | only routes on the stack render | flat |
| tabs in one navigator | ~100 (+13%); 300: +24%, +48MB, +160ms startup | ~0.07ms JS per tab per action | flat (More tab) |
| stack depth (mounted) | ~30–40 deep; 60: +41% render, +83% JS | native-stack walks the route list O(k), plus a bigger heap | +5% |
| enableFreeze | JS growth +83%→+28%; wall and memory unchanged | frozen screens stay mounted | n/a |
| screen size (rows 10→160) | +85ms, the biggest lever | views per screen | small 1.4× faster than RN, big 0.7× (Auto Layout) |

RN fixed costs: about 15ms per push (Hermes + Fabric + rn-screens), +32MB runtime memory, about 3.3MB per stacked screen (native about 2.3MB).
Startup was equal (about 1.5s on the simulator). Hitches were equal except on deep stacks (86 vs 69 per 60 pushes).
