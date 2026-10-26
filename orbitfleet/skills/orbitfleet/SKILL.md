---
name: orbitfleet
description: Lease an iOS simulator from the local orbitfleet daemon, record a test run on it (device log, app output, crashes, screenshots), debug it (lldb, Instruments, video), and give it back. Use when an agent needs its own simulator, says "claim a sim", "orbitfleet", "run on a fleet sim", "attach lldb to the sim app", "profile on a fleet sim", or several agents test in parallel on one Mac.
---

# orbitfleet

One agent, one simulator, one lease. Every command takes `--json` and exits non-zero on failure; an error comes back
as `{"error": {"code", "message"}}` (codes: `capacity`, `no-device`, `not-holder`, `not-leased`, `run-active`,
`run-ended`, `bad-request`, `prepare`, `busy`).

```sh
export ORBITFLEET_HOLDER="<project>:<task>"                     # shown to the person on the sim's card
L=$(orbitfleet claim --purpose "login flow" --wait 10m --json)  # -> {"udid", "name", "expires", "warning"?}
U=$(jq -r .udid <<<"$L")
xcrun simctl install "$U" path/to/App.app                       # your build
R=$(orbitfleet run start "$U" --label "login flow" --app com.example.app --json | jq -r .id)
orbitfleet run launch "$R"                                      # stdout, stderr, NSLog land in the run, live
# drive the app: argent tools with udid=$U, or a simwalk flow with --udid $U
orbitfleet run mark "$R" "tapped Sign in"                       # one line per meaningful step
orbitfleet run shot "$R" after-login                            # AVIF into the run
orbitfleet run stop "$R" --status passed                        # or --status failed --reason "..."
orbitfleet release "$U"
```

## Rules

- Use only the udid you claimed. Never boot, erase or reconfigure another simulator.
- Always `run stop` then `release`, also on failure. The person reads the run, not your chat.
- Mark steps as you go: the person debugging later reads the timeline next to the device log.
- Any command naming the udid (or its run) renews the lease, 30 min by default (`claim --ttl 60m` for longer);
  `orbitfleet renew <udid>` does only that. An expired lease frees the sim and aborts its run.
- `claim` fails with `capacity` when every pool sim is leased or the Mac is full: wait (`--wait`), never boot your own.
- Launch with `orbitfleet run launch` so app output reaches the run. argent's `launch-app` works too, but then the run
  has only the device log. A crash during the run is collected at `run stop` (matched on the `.ips` bundle id).
- `claim` clears a previous holder's `DYLD_INSERT_LIBRARIES` (argent's dylib). A `warning` on the lease means an
  attached argent server re-injected it: fine for UI work, not for timings (orbit-SP1 HARNESS rule 2).

## Debug a run

```sh
orbitfleet debug "$R" lldb                     # prints `xcrun lldb -p <pid>`; --open runs it in Terminal (for a person)
orbitfleet run launch "$R" --wait-for-debugger # held before main; attach, then `continue`
orbitfleet debug "$R" profile --time-limit 30s # Instruments Time Profiler -> profile-<n>.trace in the run (blocks)
orbitfleet debug "$R" video start              # ... then: debug "$R" video stop -> video-<n>.mov
orbitfleet run logs "$R" --grep error          # the device log; run show "$R" lists every artifact
```

Apple's own simulator apps refuse lldb ("Not allowed to attach"); apps you build attach. A trace taken while argent's
dylib is injected carries a `note`.

## The daemon

You never manage it. The first command starts it; a command from a newer build of the binary restarts it once no run
is active and no claim is in flight (leases survive); only one daemon runs per state dir. `orbitfleet version` shows
both builds. The person watches every sim, run and log at `orbitfleet ui` (127.0.0.1:7421, loopback only).

Contract (JSON shapes, decisions, measured numbers): `orbitfleet/DESIGN.md` in the orbit-SP1 checkout (`$ORBIT_HOME`,
else `~/code/orbit-sp1`). When to use a slim pool sim vs a regular one: `rules/orbitfleet.md` there.
