---
name: orbitfleet
description: Lease an iOS simulator from the local orbitfleet daemon, record a test run on it (device log, app output, crashes, screenshots), and give it back. Use when an agent needs its own simulator, says "claim a sim", "orbitfleet", "run on a fleet sim", or several agents test in parallel on one Mac.
---

# orbitfleet

Working now: `claim`, `release`, `renew`, `ls`, `run start|launch|mark|shot|stop|show|logs`, `runs`, `debug lldb|profile|video`, `ui`,
`version`.
The person watches every sim, run and log at `orbitfleet ui` (127.0.0.1:7421); a mark is the line they read first. Contract: ../../DESIGN.md.

One agent, one simulator, one lease. Every command takes `--json` and exits non-zero on failure.

```sh
export ORBITFLEET_HOLDER="<your agent name or task>"            # shown to the person on the sim's card
L=$(orbitfleet claim --purpose "login flow" --wait 10m --json)   # -> {"udid": ..., "name": ..., "expires": ...}
U=$(jq -r .udid <<<"$L")
R=$(orbitfleet run start "$U" --label "login flow" --app com.example.app --json | jq -r .id)
orbitfleet run launch "$R"                                       # app stdout/stderr go to the run
# drive the app: argent tools with udid=$U, or a simwalk flow with --udid $U
orbitfleet run mark "$R" "tapped Sign in"                        # one line per meaningful step
orbitfleet run shot "$R" after-login
orbitfleet run stop "$R" --status passed                         # or failed --reason "..."
orbitfleet release "$U"
```

Rules:
- Use only the udid you claimed. Never boot, erase or reconfigure another simulator.
- Any command naming the udid renews the lease (30 min); `orbitfleet renew <udid>` does only that. A lease that
  expires frees the sim (and, from P2, aborts its run).
- Always `run stop` then `release`, also on failure. The person reads the run, not your chat.
- Mark steps as you go: the person debugging later reads the timeline next to the device log.
- Debugging: `orbitfleet debug <run> lldb` prints the attach command (`--open` runs it in Terminal; Apple's own apps
  refuse lldb, yours do not); `run launch --wait-for-debugger` holds the app before `main`; `debug <run> profile
  --time-limit 30s` records an Instruments trace into the run (a note says when argent's dylib skews it);
  `debug <run> video start|stop`.
- Launch the app with `orbitfleet run launch` so its stdout/stderr (and NSLog) land in the run. argent's `launch-app`
  works too, but then the run has only the device log. A crash during the run is collected at `run stop`.
- `claim` clears a previous holder's `DYLD_INSERT_LIBRARIES` (argent's dylib). If the lease JSON has a `warning`, an
  argent server is attached and re-injected it: fine for UI work, but do not take timings there (HARNESS rule 2).
- `claim` fails with `capacity` when the Mac is full; wait (`--wait`) instead of booting your own simulator.
