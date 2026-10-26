# orbitfleet dev log

## 2026-10-25 claude (orbit plan session)
did:      DESIGN.md (D1-D10, contract, debug table, phases P1-P5); .orbit/ installed (orbit-SP1 b667208), orbit.toml (Go
          units = packages), principles NF-1..NF-5 (observed); skills/orbitfleet/SKILL.md draft; UI mock (Fleet, Runs) on a Design canvas, linked from DESIGN.md
gate:     .orbit/orbit verify fast -> FAIL (vet, test: no go.mod yet, expected until P1; gofmt, principles PASS). Probe with bad code in scratch: NF-1..NF-4 each
          fire with file:line (fleet/lease.go:4 os/exec, :5 simslim; sim/sim.go:3 net/http; web/web.go:3 EraseDevice)
floor:    breadth 0/16 commands (measured: none exist) · ready 0/5 phase gates
          measured inputs: screenshot jpeg 0.37 s; debug log 1.64 MB/15 s -> zstd 29 KB; AVIF shot 96 KB vs png 3.2 MB
next:     P1: write fleet lease tests (50 goroutines, fake clock) and scripts/accept.sh claim/release, both failing
ask:      name "orbitfleet" ok? pool = sims named fleet-NN, created by hand with `simslim clone` (out of scope v1)?

## 2026-10-25 claude (P1 leases)
did:      renamed simfleet -> orbitfleet (user). go.mod `orbitfleet`, simslim pinned to 2c68da9
          (v0.12.3-0.20261025112835-2c68da93700a: main's offline slim path, faster first claim than v0.9.0).
          fleet/api.go + fleet/fleet.go (reserve-then-prepare under one mutex, booted-first, MaxBooted counts every
          booted sim plus leases, wait via close-and-replace channel, TTL per lease, Save hook), web/web.go (handler,
          0600 unix socket, refuses a live socket, replaces a stale one, client), sim/sim.go (simslim EnableSlim or
          NoReboot, DYLD unset with 3 tries -> warning), main.go (CLI, daemon auto-start, leases.json atomic),
          scripts/accept.sh (own pool ofaccept-01/02, own daemon + ORBITFLEET_HOME)
gate:     .orbit/orbit verify fast -> PASS (vet, test -race in fleet+web, gofmt, principles 0 violations)
          .orbit/orbit verify live -> PASS twice (accept 7/7; ui-flow planned)
floor:    breadth 5/17 commands (measured: claim release renew ls serve) · ready 1/5 phase gates (P1)
          claim latency, 2 parallel: new sims 32 s, shut-down slim 12 s, booted 3 s (measured: accept.sh)
found:    first live rerun FAILED: claim's prepare saw argent's libArgentInjectionBootstrap.dylib come back right after
          unsetenv. argent tool-servers (3 running, other sessions) had attached native-devtools to both idle test sims
          (/private/tmp/argent-nd-33E30BC8.sock, -245EA5B3.sock), no app launched, no claim. Fix: the lease warns
          instead of failing (D5); accept step 4 asserts our planted value is gone, or the lease warns.
next:     P2 runs + logs: write the accept steps first (run start/launch/mark/shot/stop on ofaccept-01, kill -ABRT -> .ips)
ask:      idle booted pool sims attract other agents' argent (your argent rule says "prefer a running device").
          Shut pool sims down on release by default (claims then take ~12 s, not 3 s)? Or point the argent rule at
          `orbitfleet claim`?

## 2026-10-25 claude (P2 runs + logs)
did:      e62e42b committed P1 (user: yes). runs/api.go + runs/runs.go + runs/files.go (one active run per sim, run.json
          saved on every change, lock released around simulator calls, crash .ips matched on header bundleID and
          mtime, restart aborts running runs), sim/capture.go (log stream through a daemon-owned pipe in its own
          process group, launch with capture in the device's data/tmp + OS_ACTIVITY_DT_MODE, AVIF shots, zstd),
          web/runs.go (run routes; any run command renews its sim's lease; release aborts the run), runs_cmd.go,
          fleet.Renew(udid, holder) holder check, accept.sh P2 steps
gate:     .orbit/orbit verify fast -> PASS; verify live -> PASS twice (18 checks; ui-flow planned)
floor:    breadth 13/17 commands (measured: + run start launch mark shot stop show logs, runs) · ready 2/5 phases
          run capture: device log 4.66-4.83 MB -> 175-182 KB zst; AVIF shot 37.6 KB (measured: accept.sh)
found:    1. simctl launch --stdout/--stderr resolve inside the simulator: host paths are silently never created
             (probe on ofaccept-01); only <device data>/tmp works, so capture lives there until stop.
          2. kill -ABRT on a sim app writes ~/Library/Logs/DiagnosticReports/<App>-<ts>.ips; its first line is JSON
             with bundleID. BSD find has no -newermt @epoch (my probe's first miss).
          3. accept.sh: pgrep exits 1 on no match; with pipefail + set -e the script died silently. Fixed.
next:     P3 UI: web tests for loopback-only TCP + token + Host check first, then web/index.html from the canvas mock
ask:      still open: idle booted pool sims attract other sessions' argent (shut down on release, or change the rule)

## 2026-10-26 claude (P3 browser UI)
did:      ~/.claude/rules/orbitfleet.md (user: yes, point agents at orbitfleet claim) + one line in rules/argent.md's
          device_selection_rule; ~/.local/bin/orbitfleet -> repo binary; pool fleet-01..04 (iPhone 17 Pro, iOS 26.4).
          web/guard.go (ServeTCP loopback IPs only, Guard: Host check, /login cookie HttpOnly SameSite=Strict, writes
          need X-Orbitfleet), web/ui.go (state, SSE, thumbnails cached 2 s with 4 at once, log tail by offset, events,
          artifact files only from the run's list, open/reveal), web/static/*, fleet.Overview (+waiting),
          runs Tail/Events/Latest, sim Stats/Thumb/OpenSimulator/Reveal, serve --port, ui.token, `orbitfleet ui`,
          scripts/ui-flow.sh wired as [checks.ui-flow]
gate:     .orbit/orbit verify fast -> PASS; verify live -> PASS twice (accept 18/18, ui-flow 10/10)
floor:    breadth 14/18 commands (measured: + ui) · ready 3/5 phases
found:    1. simctl `io screenshot ... -` writes a file named "-" in the cwd on Xcode 27 (help says stdout): blank
             thumbnails and stray files in orbit-sp1/ and orbitfleet/ (removed). Thumb now uses a temp file;
             ui-flow asserts no ./- appears.
          2. selectors passed while the page was wrong: a screenshot showed a blank thumbnail and a stray "null".
             ui-flow now asserts naturalWidth > 0 and no "null" in the timeline.
          3. setting img.src every 3 s cancels a slower screenshot (busy sim) so it never shows: load off-screen, swap.
          4. finish() wrote the final status last, so a read during zstd saw "running": status first now (test added).
          5. zstd -19 vs -15 on a real 7.4 MB log: 3.64 s vs 0.13 s for 121 vs 137 KB: moved to -15 (D8).
next:     P4 debug: write the accept steps first (lldb pid = launchctl list pid, -w launch stays suspended, 5 s trace)
ask:      a running daemon keeps the old binary after a rebuild (`pkill -f "orbitfleet serve"` restarts it on the next
          command). Add `orbitfleet restart`, or have the CLI restart a daemon older than its binary?

## 2026-10-26 claude (auto-restart + P4 debug)
did:      daemon.go: connect() restarts a daemon running an older build of the same binary (resolved path + mtime
          from /api/version) with its own args; /api/restart refuses (busy) while a run is active or a claim is
          preparing or queued (D11). daemon.pid (removed only if still ours), `orbitfleet version`.
          runs/debug.go: LLDB (command + optional Terminal via lldb.command), Profile (xctrace, note when DYLD is
          set), Video (start/stop, stopped by run stop too); sim/debug.go; web debug + open routes; UI Debug buttons
          (Attach LLDB, paused relaunch, Profile 30 s, Record/Stop video) and Open for traces and videos.
          testdata/hello + scripts/testapp.sh: a swiftc-built, ad-hoc signed fixture app. Launch now sets
          NSUnbufferedIO so print() reaches app.out live.
found:    1. Apple's simulator apps refuse lldb ("Not allowed to attach"); an ad-hoc signed app does not.
          2. Signing the fixture with a get-task-allow entitlement wedged ofaccept-01: install stuck as a
             placeholder, simctl launch and get_app_container hung. Shutdown recovered it; plain ad-hoc signing works.
          3. print() to a captured stdout is block-buffered: "hello stdout" arrived only at exit. NSUnbufferedIO=YES
             (what Xcode sets) fixes it for every app.
          4. recordVideo ignored a SIGINT sent before "Recording started" (under load after a trace): StartVideo
             now waits for that line (~1.4 s).
          5. agent-browser: a command right after `close` on the same session fails (EOF / os error 35); each
             ui-flow run uses its own session.
          6. orbit verify kills a check at 300 s by default; accept now takes ~3 min: timeout 900 set.
          7. rules/argent.md was rewritten since P3 (the orbitfleet pointer line is gone); rules/orbitfleet.md
             stands, and another session extended it with slim/full modes (argent-sim). orbit-SP1 now owns user
             rules (rules/orbitfleet.md there, `bin/orbit sync`): edit the SP1 copy from now on.
          8. ui-flow failed only under `orbit verify`, as "Failed to read: ... (os error 35)": that is agent-browser's
             `wait` timing out, because the page never connected. Not stdin and not pipes: with other sessions busy,
             `simctl list` took 1.0-1.5 s (66 simctl processes; ~0.1 s idle), and every page's SSE computed its own
             state before sending headers, every thumbnail its own FindDevice. Now one loop shares the snapshot with
             all pages while any watches, headers go out at once, and sim caches each device's set from List.
          9. The persistent ui-flow failure was mine: an accept run killed by orbit verify's 300 s timeout (SIGKILL, no
             trap) leaked its daemon, and two ui-flow daemons leaked on the fixed port 7499. Later runs could not bind
             it, curl's 401/403 checks passed against the leaked daemon, and the sign-in token was the new one's, so
             the page never connected. Now: a random free port per run, the listener's pid must be our daemon's,
             and each script starts a watchdog that stops its daemon if the script is killed.
          10. A test daemon ignored SIGTERM at load ~400 (its shutdown stalled; the stack dump went to a deleted log).
             serve now exits 15 s after a stop no matter what.
          11. Host load on 16 cores reached 591 (other sessions' rustc/swift builds); accept went 190 s -> 646 s, and
             lldb-continue and video steps timed out. The live gate now waits for load < 48 before it runs.
