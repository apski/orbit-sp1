# orbitfleet: design

A local daemon that leases slim iOS simulators to agents, records every run (device log, app output, crashes,
screenshots), offers debug attach per run, and shows the whole fleet to the person in a browser. simslim does the
slimming in-process; orbitfleet adds ownership, runs and a view.

Status: P1-P5 built and passing 2026-10-26 (P5: 8 parallel agents, 8/8). Lives in orbit-SP1 (`orbitfleet/`). Dev log: `DEVLOG.md`. House rules: `.orbit/principles.toml`.

## Who uses what

| Who | Surface | Does |
|---|---|---|
| Agent | `orbitfleet <cmd> --json` (skill: `skills/orbitfleet/SKILL.md`) | claim a sim, start/stop runs, mark steps, screenshot, release |
| Person | `orbitfleet ui` → `http://127.0.0.1:7421` | watch every sim live, read a run's logs, attach a debugger, force-release a stuck lease |

```
agent ── CLI ── unix socket (0600) ──┐
                                     ├── orbitfleet serve ── sim ──┬── xcrun simctl / log / xctrace / lldb
person ── browser ── 127.0.0.1:7421 ─┘        │                  └── simslim (Go library, in-process)
                                              └── ~/Library/Application Support/orbitfleet/{leases.json, runs/<id>/}
```

Packages (each is an Orbit unit `go-pkg:<dir>`): `fleet` (leases, capacity, pure), `runs` (run lifecycle, capture),
`sim` (the only adapter to xcrun and simslim), `web` (HTTP, SSE, embedded `web/index.html`). `main.go` wires the CLI.

## Decisions

| Id | Decision | Why | Rejected |
|---|---|---|---|
| D1 | One Go binary that imports simslim as a library. Deps: simslim only (it has none). | simslim's root package exposes everything needed with no terminal stack (table below). | SwiftUI app (simslim ships one; agents still need a CLI, so two front ends). Electron (new runtime). |
| D2 | A daemon owns leases; the first CLI call auto-starts it (`serve` detached). | One arbiter, no races. | flock files: agent Bash calls exit at once, so a process-scoped lock releases immediately. |
| D3 | `sim` is the only package that runs a process or imports simslim. | One file to audit what touches simulators; fleet tests use a fake `sim`. Mirrors simslim's own `simctl.go` rule. Rules NF-1, NF-2. | |
| D4 | HTTP only in `web`. One handler, two listeners: unix socket for agents, loopback TCP for the browser. | fleet/runs stay callable without a server. Rule NF-3. | MCP server: add when an agent cannot shell out. |
| D5 | Lease = udid + holder + TTL 30 min, renewed by every command that names the udid. Expiry aborts the active run. `claim` (not `release`) unsets `DYLD_INSERT_LIBRARIES` in the sim's launchd, so every path to a new holder (release, expiry, daemon crash) is covered. If an attached argent server re-injects it at once, the lease carries a `warning` instead of failing. | Agents die mid-task; a sim must come back by itself. argent leaves its dylib in launchd (orbit-SP1 HARNESS rule 2), which poisons timings. Found in P1: argent tool-servers from other sessions attached native-devtools to both idle test sims (`/private/tmp/argent-nd-<udid>.sock`) without a claim and re-set the variable right after our unsetenv. | heartbeat thread in the agent (agents don't have one). |
| D6 | Zero inbound ports. TCP binds `127.0.0.1` only; Host header must be `127.0.0.1:7421` or `localhost:7421` (DNS rebinding); `/login?t=<token>` (token in a 0600 file; `orbitfleet ui` opens that link) sets an HttpOnly SameSite=Strict cookie every other request must carry; writes also need an `X-Orbitfleet: 1` header a cross-site form cannot set. Enforced by Go tests in `web/` and checked live by `scripts/ui-flow.sh` (`lsof` shows only 127.0.0.1). | A web page in the person's browser can reach loopback; mutating routes boot and launch things. | 0.0.0.0 / LAN sharing (out of scope; would be scanned within days). |
| D7 | Never erase, delete, disk-clean or un-slim. | Agents get leases, not ownership. Destructive ops stay in simslim's app behind its confirmations. Rule NF-4. | |
| D8 | Files, no DB: `leases.json` + `runs/<id>/run.json` and artifacts; run list is a dir scan. Logs zstd `-15 --long` at stop (not -19: see Measured), stored screenshots AVIF, live thumbnails JPEG in memory only. | Numbers below. | SQLite (cgo or a new dep for a list of runs). |
| D9 | The agent contract is the CLI's JSON; all output structs live in `fleet/api.go` (later `runs/api.go`). Changing a field is a breaking change. | Same rule simslim uses for `output.go`. | |
| D10 | Capacity: `--max-booted`, default min(4, (host RAM − 6 GB) / 1 GB). A claim beyond it waits (`--wait 10m`) or fails with `capacity`. | A slim sim measures ~0.9 GB (simslim README), so RAM would allow 16, but CPU and simctl contention come first: load 677 with 11 sims booted (2026-10-26). 4 is the user's cap. Queueing beats swapping. | |
| D11 | The CLI restarts a daemon running an older build of its own binary (same resolved path, older mtime, from `/api/version`), with the daemon's own args. The daemon refuses (`busy`) while a run is active or a claim is preparing or queued; the CLI then warns and the next command retries. | A rebuild otherwise ran the old daemon until someone killed it. Restarting mid-run would abort the run (its capture processes cannot move to the new daemon). Leases survive (`leases.json`). | a manual `orbitfleet restart` (asked for auto, 2026-10-26). |

## Measured here (2026-10-25, Xcode 27.1, iPhone 17 Pro iOS 26.4, already-booted sim)

| What | Number | Decision |
|---|---|---|
| `simctl io screenshot` latency | png 0.48 s, jpeg 0.37 s | thumbnails: jpeg, one per sim every 2 s, 4 workers |
| Screenshot size (1206×2622) | png 3,239,188 B · jpeg 519,280 · AVIF q60 95,834 (−97% vs png) · JXL lossless 1,555,348 (−52%) | stored run screenshots: AVIF (`avifenc -q 60 -s 6`); browsers decode it |
| Device log, idle sim, `--level debug` | 1,636,797 B / 15 s (≈390 MB/h) → zstd -19 --long 28,951 B (−98%); gzip -9 69,400 B | default level `info`; zstd at run stop |
| Device log, `--level info` | 884,973 B / 15 s (≈210 MB/h raw) | live file stays raw so the UI can tail it |

Command for the log rows: `xcrun simctl spawn <udid> log stream --style ndjson --level <l>` for 15 s, then
`zstd -19 --long`. Rerun with an app under load before trusting the per-hour figures.

Claim latency (P1, `scripts/accept.sh`, two parallel claims, `claims took`): new sims 32 s, shut-down slim sims 12 s,
already booted 3 s.

Run capture (P2, `scripts/accept.sh`, Settings launched then `kill -ABRT`): device log at info 4.66-4.83 MB in a few
seconds -> zstd 175-182 KB (-96%); app stderr 45 KB in 3 s with `OS_ACTIVITY_DT_MODE`; AVIF screenshot 37.6 KB.

zstd level (P3, a real 40 s debug log, 7,402,368 B, `zstd -<l> --long -T0`): -19 3.64 s 120,914 B; -15 0.13 s
137,434 B; -12 0.07 s 141,758 B. -15 is 28x faster for 14% more bytes; at an hour of logging -19 would hold
`run stop` for minutes, so P3 moved to -15.

## Data model

```
Lease { udid, name, holder, purpose, since, expires, run_id? }
Run   { id, udid, holder, label, app?, pid?, status: running|passed|failed|aborted, reason?, started, ended?,
        artifacts: [{kind, path, bytes}] }
runs/<id>/  run.json  device.ndjson(.zst)  app-<n>.out  app-<n>.err  events.ndjson  shots/*.avif  crash/*.ips
            profile.trace  video.mp4
```

`events.ndjson` holds marks, launches, screenshots and debug actions in one timeline, so the UI and a later agent read
one file.

## CLI (the agent contract, every command takes `--json`, exits non-zero on failure)

```
orbitfleet claim --holder <name> [--purpose "..."] [--device <udid|name>] [--runtime 26.4] [--profile ci.json]
               [--requires push,storekit] [--ttl 30m] [--wait 10m]          -> Lease
orbitfleet release <udid> [--force] [--shutdown]
orbitfleet renew <udid>                                                      the UI's Extend
orbitfleet ls                                                                  -> devices + leases + active runs
orbitfleet run start <udid> [--label "..."] [--app <bundle>] [--log-level info|debug] [--predicate '<log predicate>']
orbitfleet run launch <run> [--wait-for-debugger] [-- <app args>]              -> {pid}
orbitfleet run mark <run> "<text>"                                             agent notes, shown in the timeline
orbitfleet run shot <run> [name]
orbitfleet run stop <run> --status passed|failed [--reason "..."]
orbitfleet run show <run> | run logs <run> [--follow] [--grep x] | runs [--holder x] [--since 1d]
orbitfleet debug <run> lldb [--open]                                           -> command, or opens Terminal
orbitfleet debug <run> profile [--template "Time Profiler"] [--time-limit 30s]
orbitfleet debug <run> video start|stop
orbitfleet serve [--port 7421] [--max-booted n] [--pool-prefix fleet-] [--profile ci.json]
orbitfleet ui [--print]                                                        opens the browser, signed in
orbitfleet version                                                             this binary and the running daemon
```

Claim picks, in order: the named device; else a free pool device (name starts with `--pool-prefix`) that is booted
and slim; else booted; else shut down (boots it). Then: `BootAndWait` if needed, `VerifyProfile`, and on drift
`EnableSlim` (persistent runtimes) or `EnableSlimNoReboot` (older ones). Holder defaults to `$ORBITFLEET_HOLDER`.

## Debug attach per run

| Option | How | Phase |
|---|---|---|
| Live device log | `log stream --style ndjson` into `device.ndjson`; UI tails it over SSE with text/level/process filters | P2 |
| App stdout/stderr | `simctl launch --terminate-running-process --stdout=… --stderr=…` with `SIMCTL_CHILD_OS_ACTIVITY_DT_MODE=enable` (os_log/NSLog mirrored to stderr, as in Xcode's console). simctl resolves those paths inside the simulator: a host path is silently never created, so capture goes to `<device data>/tmp/orbitfleet/` and moves into the run folder at stop | P2 |
| Crash reports | at stop, copy `~/Library/Logs/DiagnosticReports/*.ips` whose process is the run's app and mtime is inside the run | P2 |
| Screenshot | `simctl io screenshot --type=png` → AVIF | P2 |
| LLDB attach | sim apps are host processes: `xcrun lldb -p <pid>`; `--open` writes `lldb.command` and opens it in Terminal (no Automation permission, unlike osascript). Apple's own simulator apps refuse ("Not allowed to attach"); any app you build does, ad-hoc signed is enough (`testdata/hello`) | P4, built |
| Attach before `main` | `run launch --wait-for-debugger` (`simctl launch -w`, the app sits in state `T`), then `debug lldb` and `continue` | P4, built |
| Instruments | `xctrace record --template … --device <udid> --attach <pid> --time-limit 30s --output runs/<id>/profile-<n>.trace` (blocks for the limit, at most 10 m); a trace taken while `DYLD_INSERT_LIBRARIES` is set carries a note (D5) | P4, built |
| Video | `simctl io recordVideo --codec=hevc` into `video-<n>.mov` (a QuickTime movie), stopped with SIGINT; run stop stops it too. The UI opens traces in Instruments and videos in QuickTime | P4, built |
| Open in Simulator.app / reveal run folder | `open -a Simulator --args -CurrentDeviceUDID <udid>` / `open -R` | P3 |
| Later | React Native DevTools URL from Metro `/json/list`; Safari Web Inspector hint for WKWebView | after P5 |

## UI (`web/index.html`, no framework, no build step)

- **Fleet grid**: a card per sim: name, holder, run label + elapsed, status dot, slim badge (`managedDisabled/managedTotal`),
  RAM, live thumbnail. Header: fleet RAM vs capacity, sims booted / max.
- **Run drawer** (click a card): tabs Timeline (marks, shots, launches), Device log (live tail, filters), App output,
  Crashes; a Debug menu (the table above); lease controls (extend, release, force-release with the holder's name shown).
- **Runs**: table of past runs (status, holder, label, duration, sim), each opening the same drawer read-only.
- `data-testid` on every card (`sim-<udid>`), drawer tab, debug action and status cell; the UI flow asserts on them.

Mock (interactive, sample data): a private artifact (Fleet + Runs). Built in P3 as
`web/static/{index.html,app.js,app.css}`, embedded in the binary, fed by `/api/events` (SSE, the state every 2 s). Deep
links: `#sim=<udid>&tab=log|app|timeline|crash`, `#run=<id>`, `#runs`. Thumbnails load off-screen and swap in once
loaded (a slow screenshot otherwise never shows). Footprint per sim refreshes every 10 s (it walks process trees).
One loop computes the state for all open pages while any watches: a state costs a `simctl list`, over 1 s when
other sessions keep CoreSimulator busy, so per-page polling piled up past the page's wait.
P3's Debug section has Screenshot, Open in Simulator.app and Reveal run folder; LLDB, wait-for-debugger, Instruments
and video come in P4.

## Phases

Each phase writes its acceptance test first (it fails), then the code, then the gate. Gate fast =
`.orbit/orbit verify fast` (vet, test, gofmt, principles); gate live = `scripts/accept.sh` on booted sims.

| Phase | Acceptance written first | Gate |
|---|---|---|
| P1 Leases | `fleet`: 50 goroutines claim from 3 fake sims, no udid held twice; expiry with a fake clock aborts and frees; renew extends. Live (`scripts/accept.sh`): two parallel claims get two booted slim sims; a third with `--wait 2s` gets `capacity`; non-holder release refused; a planted DYLD value is gone after re-claim (or the lease warns); leases survive a daemon restart; release `--shutdown` frees both | fast + accept. **Done 2026-10-25**, live gate passed twice |
| P2 Runs + logs | a run on a live sim produces non-empty `device.ndjson`, `app.out`, a mark in `events.ndjson`, an AVIF shot; stop compresses the log; a launched-then-killed app (`kill -ABRT <pid>`) leaves an `.ips` in `crash/` | fast + accept **Done 2026-10-25**: also one active run per sim, run start needs the lease, log stream piped through the daemon and stopped by process group (no orphan), crash matched on the `.ips` header `bundleID`; live gate passed twice |
| P3 UI | `web` tests: TCP refuses a non-loopback bind, a missing token (401) and a foreign Host (403). Browser flow: card count = booted sims; claim from the CLI makes the holder appear on `sim-<udid>` within 3 s; drawer log tail grows | fast + accept + ui-flow. **Done 2026-10-26** (`scripts/ui-flow.sh`, 10 checks: loopback-only bind, 401/403, cards, holder in 0 s, log tail grows, timeline, thumbnail loads, no stray file, force release from the UI aborts the run); live gate passed twice |
| P4 Debug | `debug lldb` prints a pid equal to the one in `launchctl list` for `UIKitApplication:<bundle>`; `--wait-for-debugger` launch stays suspended until lldb continues; `profile --time-limit 5s` writes a `.trace` | fast + accept **Done 2026-10-26** (accept.sh, on the fixture app: lldb refused before launch, pid = launchctl, the command attaches, `Ts` -> `Ss` after continue, 13 MB trace, video; ui-flow: video and paused relaunch from the drawer); live gate passed twice |
| P5 Fleet smoke | 8 agents in parallel (`claude -p` with the skill), each claim → run → mark → shot → stop → release, on 8 sims | all **Done 2026-10-26** (`scripts/p5-fleet.sh`, opt-in `fleet` set: 8 Sonnet agents given only the skill: 8/8 runs passed with shot and marks, 128 s wall, 0 overlapping runs, 0 leases left, $2.61, 0 host swap-outs, 17.1 GB peak RAM across every booted simulator) |

Floor (HARNESS rule 5): **breadth** = CLI commands implemented / 19 above (`ui`, `version` added); **ready** = acceptance checks passing, and
the measured number of agents that complete P5 in parallel with no host swap (`vm_stat` swapouts flat).

## Out of scope for v1

Creating or cloning pool sims (use `simslim clone <golden> fleet-NN`; clones carry the profile and installed apps),
real devices, Android, sharing devices off this Mac, run retention, MCP, multi-user auth.

## simslim API used (Orbit `refs`, simslim @ 2c68da9)

| Need | Function |
|---|---|
| devices | `ListDevices` simctl.go:182, `FindDevice` simctl.go:220, `Device` simctl.go:24 |
| boot/shutdown | `BootAndWait` simctl.go:329, `Shutdown` simctl.go:341 |
| slim on claim | `LoadSlimProfile` profile_file.go:23, `BuildProfile` profile_file.go:88, `VerifyProfile` verify.go:23, `EnableSlim` slim.go:210, `EnableSlimNoReboot` slim.go:221, `PersistentOverridesSupported` slim.go:169 |
| fleet view | `FleetSnapshot` fleet.go:12 → `TopOutput` output.go:84 / `TopSim` output.go:72, `MeasureMany` measure.go:81 |
| feature preflight | `ResolveFeatures` features.go:67, `DiagnoseFeatures` features.go:81 (claim `--requires push`) |
| progress | `Reporter` slim.go:14 (stream into the run's `events.ndjson`) |
