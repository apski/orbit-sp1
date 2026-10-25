# Measurement hygiene: what went wrong and how it was caught

Each entry: symptom → evidence → fix. All from ~/code/rn-vs-native, 2026-10-24.

## End points
- **Fabric onLayout ≠ on screen.** Symptom: depth 1 measured slower than depth 2. Main-thread CPU inside the
  "render" interval was 8–29ms with a stack at the root but 0–2ms under tabs. Cause: onLayout fires from the
  shadow tree, so the main-thread mount sometimes landed after the interval ended. Fix: a native `LabProbe` view
  ends the interval after didMoveToWindow plus one runloop turn. onLayout stays as a `layout` event.
- **RN lays a pushed screen out twice** (about 20ms and about 50ms after push: native-stack corrects the frame). When
  linking events, take the first one and count them.
- **Pop end points differ by framework.** RN unmounts about 8ms after goBack, mid-animation; UIKit's
  viewDidDisappear fires at animation end. Report pop per arm, never as a ratio.

## Contamination
- **Accessibility on** (any ax-service attached): UITableView created all 40 rows (NSLog count in cellForRow), and only 2
  once detached. RN builds AX trees too. The first sweep ran with simwalk's ax-service alive, so it was invalid.
- **Host load**: other agents' simulators (mediaanalysisd at 650% CPU) pushed load to 134. Gate on load, log it per run.
- **Cold first launch** after reinstall adds about 500ms to startup.

## Tooling failures
- **xctrace wedges** past its own `--time-limit` (once for 23 min). Hung traces are about 40KB with only RunIssues.storedata,
  so recording never started. Two signatures: the app never launched, or the app ran and exited while xctrace stayed alive.
  Detect both by polling `pgrep -x <process>`.
- **build.sh installed a stale app** after a failed build (`grep || true` swallowed the exit code). Use pipefail and
  check the build log.
- **The analyzer overwrote a sweep's CSVs** when run on one trace in the sweep directory. Single-trace mode prints only.
- **Test harness bug**: patching `subprocess.Popen` through `sweep.subprocess` patches the global module, so every
  `run()` became the mock. Patch conditionally on the command.

## Attribution
- Leaf-first bucket matching put Fabric-caused `-[UIView initWithFrame:]` into "uikit" and DYLD-STUB frames into
  "dyld". Native screen building landed under "uikit-nav" because UIKit calls loadView inside the transition.
  Fix: rule priority over the whole stack plus an `app-native` bucket for the twin's own code.
- Swift conformance lookups under `UINavigationBar setItems` are UIKit's own transition cost, which both arms pay.

## Twin fairness
- A UIStackView wrapping a UITableView makes the table materialize every row. Use plain constraints.
- UITabBarController collapses tabs beyond 5 into "More", so native many-tabs is cheaper by construction. Say so.
- The native nested-stack descend animated each push (about 590ms), which isn't comparable to RN's single navigate. Fix:
  push intermediate levels unanimated.
- The native form and grid used non-virtualized UIStackView Auto Layout. That is idiomatic, but a UICollectionView would beat it.
