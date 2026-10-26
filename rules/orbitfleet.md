---
description: Claim iOS simulators through orbitfleet so parallel agents never share one; overrides argent's "prefer a running device" for iOS simulators
alwaysApply: true
---

<orbitfleet_rule>
One agent, one simulator, and a slim one by default. The person picks the mode with `argent-sim slim|full|status`
(stored in `~/.argent/sim-mode`, default `slim`):

- **slim** (default): lease from orbitfleet's slim pool (below). ~3-4x fewer processes than a regular sim, booted
  headless (argent's MCP env has `ARGENT_SIMULATOR_NO_WINDOW=1`). Measured 2026-10-26: argent launch, describe,
  tap and navigation all work on a slim sim.
- **full**: skip orbitfleet and use argent's device_selection_rule with a regular simulator and its window. Use it
  too, without changing the mode, when the person asks for a full / visible simulator in this task, or the task needs
  a system service the slim profile turns off (push, iCloud, ...): use full for that task and say why.

In slim mode, before booting or driving an iOS simulator (argent, simctl, xcodebuild test, simwalk), when
`command -v orbitfleet` succeeds:

1. `export ORBITFLEET_HOLDER="<project>:<task>"`, then `orbitfleet claim --wait 10m --json` (add `--runtime 26.4` or
   `--device <name>` when the task needs one). Use only the returned `udid`: argent's `udid`, `xcodebuild -destination
   id=<udid>`, simwalk `--udid`.
2. Never pick a booted simulator you did not claim. This overrides argent's device_selection_rule ("prefer a running
   device") for iOS simulators: other agents' simulators are booted too.
3. Record the work as a run so the person can follow it in the orbitfleet UI (`orbitfleet ui`):
   `orbitfleet run start <udid> --label "<what>" --app <bundle id>`, `run launch`, `run mark <run> "<step>"`,
   `run shot`, and `run stop <run> --status passed|failed`.
4. Always `orbitfleet release <udid>` when done, also on failure.
5. If `claim` fails with `no-device` (no `fleet-*` pool on this Mac) or `capacity` after the wait, fall back to argent's
   device_selection_rule and say so in your reply.
6. A `warning` on the lease means an argent server re-injected its dylib: fine for UI work, not for timings.

Physical iPhones, Android, Chromium and TV targets are unaffected. Contract and details:
the `orbitfleet` skill (`orbitfleet/skills/orbitfleet/SKILL.md` in orbit-SP1).
</orbitfleet_rule>
