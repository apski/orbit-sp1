---
name: field-perf
description: "Production truth for NavLab's lab predictions: wire field performance telemetry into a React Native app (Sentry React Navigation instrumentation with TTID/TTFD, PostHog screen and perf events, an iOS MetricKit subscriber with per-screen signposts), normalize Sentry/PostHog/MetricKit/CSV exports to one schema, report p50-p99 per screen, metric, release and device class, and attach field metrics to ledger predictions as their measure command. Use when the user says 'field data', 'production performance', 'real users', 'RUM', 'Sentry performance', 'MetricKit', 'did the release make it faster', 'p75 in production', 'lab vs field', or when a ledger prediction needs a production judge."
---

# Field perf

The lab says what *should* happen on one device under controlled load; the field says what users got. The ledger's
real judge is the field: a move that wins in the lab and not in production didn't work.

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check field-perf` (needs a telemetry source). No telemetry yet →
start at §1; the first useful report is one release after the wiring ships.

## 1. Instrument (once per app)
Pick what the app already ships; one source is enough. Check the snippets against the installed SDK version.
- **Sentry RN** (TTID, TTFD, transition, app start): `const nav = Sentry.reactNavigationIntegration({ enableTimeToInitialDisplay: true })`,
  `Sentry.init({ tracesSampleRate, integrations: [nav] })`, `onReady={() => nav.registerNavigationContainer(navigationRef)}`
  on the `NavigationContainer`, `Sentry.wrap(App)`. TTFD: render `<Sentry.TimeToFullDisplay record={dataLoaded} />`
  in screens that load data. Transition comes from the `navigation.processing` span; app start is automatic.
- **PostHog**: send numbers, not just views: `posthog.screen(route, { ttid_ms })` and
  `posthog.capture('nav_perf', { metric: 'transition', value_ms, $screen_name: route })`. Time it like navprobe:
  dispatch (navigation `__unsafe_action__` listener) → first `onLayout` + `requestAnimationFrame` of the new screen.
  Add `device_class` if you bucket devices yourself.
- **MetricKit** (iOS, app-wide launch/hang/hitch, plus per-screen intervals): in `AppDelegate`,
  `MXMetricManager.shared.add(self)`; in `didReceive(_ payloads: [MXMetricPayload])` and
  `didReceive(_ payloads: [MXDiagnosticPayload])` upload `payload.jsonRepresentation()`. Per screen:
  `mxSignpost(.begin/.end, log: MXMetricManager.makeLogHandle(category: "transition"), name: "Checkout")` around the
  push; **category = metric name** (`transition`, `ttid`, ...), **name = screen**. Payloads arrive about daily,
  aggregated, only from users who share analytics with developers.

## 2. Ingest and report
```bash
python3 $NAVLAB_HOME/arch/field.py ingest export.json --source sentry|posthog|metrickit|csv --json .navlab/field-<date>.json
python3 $NAVLAB_HOME/arch/field.py report .navlab/field-<date>.json [--screen Checkout] [--metric transition] \
    [--device-class low] [--before 2.3.0 --after 2.4.0]
python3 $NAVLAB_HOME/arch/field.py selftest          # every source path against arch/fixtures/field
```
Exports: Sentry Discover/Explore **row-level** transactions or spans (CSV or JSON: `transaction`, `transaction.op`,
`measurements.*`, `span.op`, `span.duration`, `device.class`, `release`) or raw transaction events; PostHog events
export / API (`{results: [...]}`) or a HogQL result; MetricKit payload JSON (one or a list); any CSV with
`screen, metric, value_ms[, device, device_class, os, app_version, ts, count]` (common aliases accepted).

## 3. Reading it
- Rows per screen × metric: all, then per `app_version`, then per device class. `--before/--after` prints p50/p75
  deltas per screen; `(n<30: noisy)` means don't conclude from it.
- Judge on **p75** (what most users feel) and check **low**-class devices separately: lab wins often shrink or grow there.
- MetricKit histograms are interpolated within buckets; precision is the bucket width. `hitch_ratio` is ms of hitch
  per s of scrolling, not ms. App-wide metrics (`app_start_*`, MetricKit `hang`) report as screen `(app)`.

## 4. Lab vs field: never compare across rulers
| | lab (NavLab) | field |
|---|---|---|
| device | one simulator / top phone, cold-start controlled | the fleet: old phones, thermal throttling, low-power mode |
| data | fixtures | real account sizes, network, cache state |
| ruler | push model: `push()` → transition end; navprobe `navaction`: dispatch → state settled | Sentry `navigation.processing`; TTID: navigation start → first frame; your own `nav_perf` |
Compare **deltas** (before/after a move) within one ruler, not absolute ms across rulers. To relate them, compute
field/lab ratio on one screen measured both ways and apply it as a calibration, labelled as such.

## 5. Attach to the ledger
Predict on the field metric before the move ships; measure after the new version has enough sessions:
```bash
python3 $NAVLAB_HOME/arch/ledger.py add --feature Checkout --move rn-native-migration --metric field_transition_p75 \
  --baseline 569 --predicted 420 --lo 360 --hi 480 \
  --measure "python3 $NAVLAB_HOME/arch/field.py report .navlab/field-latest.json --screen Checkout --metric transition --stat p75 --version 2.4.0 --measure"
python3 $NAVLAB_HOME/arch/ledger.py measure <id>      # after re-exporting to .navlab/field-latest.json
```
`--measure` prints `n=..` then the number (last line, what ledger reads) and exits 1 below `--min-n` (default 20),
so a thin rollout cannot close a prediction. Baseline = the same command with the previous `--version`.

## Where it feeds
`app-architecture-steward` (field column, ledger scoring), `rn-native-migration` (stop rules on field misses),
`design-afresh` and `navlab-audit` (which screens hurt users, not just the simulator).

## Limits
- **Validated on synthetic exports only** (`arch/fixtures/field/`, shapes from SDK docs); no real production export
  has been run through it. Expect column-name drift by SDK/export version and fix the mapping when it happens.
- Sentry `navigation` transaction duration is not mapped (it runs to the idle timeout); slow/frozen frame counts and
  JS stall measurements are not mapped either. Aggregated exports (`p75(...)` columns) are rejected.
- A PostHog `$screen` `duration` is usually dwell time and is ignored; send `<metric>_ms` properties.
- Device class without an SDK-provided one is guessed from the iPhone model id only; Android → `unknown`.
- MetricKit: iOS only, opt-in users only, daily aggregates; `TimeToFirstDraw` is mapped to `app_start_cold`, resume to `app_start_warm`.

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill field-perf --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
