---
name: orbit-route
description: "Decide where a job runs, from a Claude Code session on this Mac: work that needs macOS (simulators, Xcode, Instruments, argent, simwalk, orbitfleet) stays local, everything else (test suites, builds, Orbit index/verify, Voyager, backtests, long streams) goes to a host (linux-box) through orbit cloud, and whole agent streams can run there in their own Claude Code or Codex session. Use when the user says 'where should this run', 'route jobs', 'run it on linux-box', 'offload', 'run a stream on the linux box', 'keep sims local', 'the Mac is slow', before a long or CPU-heavy command, or when starting parallel streams."
---

# orbit-route: one Mac brain, Linux muscle

This Claude Code session on the Mac stays the orchestrator. It decides per job where the job runs, using
`orbit cloud` (skill `orbit-cloud`, `$(.orbit/home)/bin/cloud.mjs`). Targets are in `~/.orbit-cloud/hosts.json`
(machine-local): today `linux-box` (i9-9900K, 16 threads, 62 GB, idle). Reach it with `ssh linux-box` on the home
LAN or `linux-box-remote` through vpn-jump; cloud picks whichever answers.

## The decision

| The job | Where | How |
|---|---|---|
| Touches a simulator, Xcode, Instruments, argent, simwalk, orbitfleet, `spectra verify`, codesign | **local** | run it as usual (orbitfleet lease, cap 4) |
| Needs this Mac's logins or secrets (Chrome profile, Keychain, `.env`, prod DB) | **local** | never sync secrets to a target |
| Short (< ~10 s) or interactive | **local** | the sync costs more than it saves |
| Test suite, build, lint, typecheck, `orbit index` / `verify`, Voyager validate/backtest, cargo, jest, pytest | **host** | `orbit cloud run --on auto -- <cmd>` |
| Long stream (minutes to hours: backtest, sweep, mutation run) | **host, detached** | `orbit cloud run --on auto --detach -- <cmd>`, then `orbit cloud jobs` / `logs <id> -f` |
| A whole workstream (an agent improving orbit-sp1, a refactor) | **an agent session on the host** | see "Streams" below |
| A burst wider than one box can take | **do** (costs money) | `orbit cloud run --on do -- <cmd>`, then `orbit cloud down` |

`--on auto` applies the first rows by itself. A command matching the macOS list (xcrun, simctl, xcodebuild, pod,
swift, argent, simwalk, orbitfleet, `run:ios`, `spectra verify`, ...) stays local, and anything else goes to the
first reachable host. When no host answers it runs locally, and it never creates a DO box on its own. It prints its
choice (`cloud: auto -> linux-box`), so you can say it in your reply. Override with `--on local`. Judge the
secrets and short-job rows yourself: auto can't see them.

## Streams on linux-box

The host has two copies of orbit-sp1:
- `~/code/orbit-cloud/orbit-sp1`: a **mirror**. Every `up` resets it to this Mac's HEAD plus working tree. Jobs run
  against it. Never edit it.
- `~/code/orbit-sp1`: a **dev checkout**, created once at this Mac's HEAD, then never touched by `up`. Agents
  improve the pack there, commit, and either push a branch to GitHub (the box has its own key) or get fetched from
  here: `git fetch linux-box` (a remote cloud added on this Mac).

Start a stream as a headless agent, detached in tmux so it survives this session:

```sh
orbit cloud run --on linux-box --detach -- 'cd ~/code/orbit-sp1 && git switch -c box/<topic> && claude -p "<the task, with its gate>" --permission-mode acceptEdits'
orbit cloud run --on linux-box --detach -- 'cd ~/code/orbit-sp1 && codex exec "<the task>"'
orbit cloud jobs --on linux-box            # running / [exit n] per job
orbit cloud attach <id> --on linux-box     # watch it live (tmux; detach with C-b d)
```

Or work there yourself: `orbit cloud ssh --on linux-box` opens zsh. Both `claude` and `codex` are installed there,
and `orbit cloud up --on linux-box` reports whether each is logged in. One branch per stream; the box's agents
follow the same HARNESS.md (gate, ratchet, owned files). Bring a stream's work back by fetching its branch and
reviewing it here before merging.

## Rules

- Say where every number was measured. linux-box is Linux on an i9: its test times are not iOS times and not
  this Mac's (HARNESS rule 2).
- Before routing, `orbit cloud ls` shows each target's load and job count. Don't pile a third heavy stream onto a
  host already running two.
- linux-box is also a GNOME desktop and the staging server (docker). Jobs go under `~/code/orbit-cloud` (on
  /srv/data). Never touch staging containers or `~/code/exampleapp/prod`, never reboot, and keep heavy jobs below
  ~12 of its 16 threads (`nice -n 10` for long ones).
- No inbound ports anywhere: a host is reached outbound over ssh only.

## Measured (2026-10-26)

Orbit engine tests (43): Mac at load 949 took 170 s wall (8.6 s CPU). The DO s-4vcpu-8gb box took 21.8 s.
linux-box took 5.3 s, or 29 s end to end through the remote jump (sync + ssh).
