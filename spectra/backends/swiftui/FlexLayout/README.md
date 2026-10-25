# FlexLayout

A Float32 Swift port of the part of Yoga that React Native layouts use. Generated SwiftUI lays out exactly like the RN
screen it came from. Plan: `docs/orbit/design/DESIGN-LAYER.md` §5.2.

```bash
# once: compile the oracle from the Yoga that RN 0.76.5 vendors (any checkout with react-native@0.76.5 in node_modules)
Y=<rn-app>/node_modules/react-native/ReactCommon/yoga
clang++ -std=c++20 -O2 -I$Y $(find $Y/yoga -name '*.cpp') oracle/native/oracle.cpp -o oracle/native/oracle
cd oracle && npm i && node gen.mjs --n 2000 --seed 1 && cd .. && swift test     # dev set: known divergences listed
node oracle/gen.mjs --n 20000 --seed 7 && swift test                             # held-out: prints PARITY n/n
```

`Tests/FlexLayoutTests/known-divergences.txt` lists the dev-set cases still known to differ (expected to fail): a new
divergence fails the test, a fixed one is printed so it can be removed.

- **Oracle:** `oracle/gen.mjs` builds seeded random flex trees and lays them out with RN 0.76.5's own vendored Yoga,
  compiled from `oracle/native/oracle.cpp` (errata All, no pixel rounding). `--oracle npm` uses `yoga-layout@3.2.1`,
  which differs from RN's Yoga on margins and absolute positioning.
- **Port:** `Sources/FlexLayout/FlexCore.swift`, function names as in `yoga/algorithm/CalculateLayout.cpp`, including
  the measurement cache. `Sources/FlexLayout/Flex.swift` is the SwiftUI `Layout` (one per composition).
- **Check:** `Tests/FlexLayoutTests/YogaParityTests.swift`, every frame within 0.01pt. `FLEX_CASE=<id> swift test` dumps
  one case; failing ids go to `cases/failed.txt`.

Status 2026-10-25: held-out seed 7 19750/20000 against RN's Yoga; 19825 against a `-ffp-contract=off` build (75 cases
are clang FMA rounding at float knife-edges). Without margins/absolute it was 20000/20000.

Supported: direction, justify (6), align (stretch/start/center/end), grow/shrink/basis/`flex`, width/height, min/max,
padding, margins, gaps, top/right/bottom/left, absolute (RN's legacy errata), measured leaves. Not yet: wrap,
percentages, aspectRatio, baseline, borders, auto margins, display:none, RTL, pixel-grid rounding.
