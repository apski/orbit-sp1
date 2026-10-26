---
name: sys-scan
description: >
  Find and clear the load that makes this Mac slow: booted simulators nobody is using (the usual cause: ~300
  processes each), leaked duplicate daemons (orbitfleet serve, argent tool-server, jest workers), zombies, stuck and
  orphaned dev processes. A read-only scan ranks them, the person picks what goes, then TERM -> KILL and a rescan.
  Use when the user says "mac is slow", "load average", "system scan", "kill idle processes", "stuck processes",
  "clean up load", "too many simulators", "what's eating CPU", "free up the machine", or when load is far above cores.
last-updated: 2026-10-26
---

# sys-scan: the machine is contended, not underpowered

Measured 2026-10-26: load 677-965 on 16 cores, 4300 processes, no single process dominant, RAM 72% free. 9 booted
simulators held ~2100 of those processes; 7 had no orbitfleet lease and ~0% CPU. Fewer simulators at once beats
offloading the same work to the cloud.

## 1. Scan (read-only, cheap)

```sh
python3 ~/.claude/skills/sys-scan/scan.py          # report; --json for the same as data
```

One `ps` pass plus each sim's `device.plist`. No `top -l` and no `simctl list`: those take minutes on an overloaded
machine and add to the load. Don't add your own heavy probes while load is above 2x cores. If it exits with "ps returned
nothing", the machine is too loaded to scan: say so and stop.

Sections: header (load vs cores, process count), SIMULATORS (processes, CPU, summed RSS, age, lease), DUPLICATES (same
command line more than once, with orphan count = ppid 1), FLAGGED (zombie, uninterruptible, stopped, RSS >= 1 GB,
CPU >= 50%, orphaned dev process >= 6 h). It excludes other users, this agent's own ancestry, and processes inside a
simulator (counted on the sim's row).

## 2. Judge each candidate

Biggest lever first: simulators, then duplicates, then single processes.

- **Simulator, `leased`**: another agent's. Never touch (orbitfleet rule). `?` = lease lookup timed out: treat as leased.
- **Simulator, no lease, ~0% CPU, hours old**: candidate. Shut it down, never erase or delete:
  `xcrun simctl shutdown <udid>`. A named project sim (NavCore ..., Orbit Design ...) loses only its running state.
- **Duplicate daemon on one port** (e.g. 8x `orbitfleet serve --port 7499`): only one can listen. Keep the listener
  (`lsof -nP -iTCP:<port> -sTCP:LISTEN`), the rest are leaked. Without a port: keep the newest, offer the orphans.
- **argent tool-server**: one server is shared by every agent. Offer only orphans (ppid 1) whose CPU is 0.
- **Worker herds** (jest-worker, multiprocessing resource_tracker): leaked when the parent runner is gone or idle for
  hours. Check the parent with `ps -o pid,stat,etime,command -p <ppid>`.
- **Zombie**: can't be killed. Kill or restart its parent, or leave it (zombies cost a PID, no CPU or RAM).
- **Uninterruptible (U)**: unkillable until its I/O returns. Report it; look for a hung disk or network mount.
- **Stopped (T)**: `kill` it, or `kill -CONT` if someone paused it on purpose.
- **launchd / pm2 / brew services managed**: `kill` makes it respawn. Use `brew services stop`, `pm2 stop`,
  `launchctl bootout`, or leave it.
- **Live CPU inside an active session** (its ancestry reaches a tmux pane or claude that is still working): leave it.
  Another agent is mid-task (a test run, a build).

## 3. Confirm, then kill

Never kill without the person's pick. Show a short plan grouped by kind with what each frees (processes, %CPU), then
AskUserQuestion with multiSelect (one option per group, recommended first). Then:

```sh
xcrun simctl shutdown <udid> ...                    # simulators
kill <pids>; sleep 5; kill -9 <survivors> 2>/dev/null
```

Rescan and report before -> after: process count, booted sims, load. The 1-minute load average lags by about a
minute, so rescan after 60 s for that number.

## 4. Stop it recurring

Say which source leaked (an agent that booted sims without orbitfleet, an acceptance run that starts `orbitfleet serve`
and never stops it) and suggest the fix at that source: cap concurrent sims (`orbitfleet serve --max-booted`), have
test harnesses kill their daemons on exit. Offload with `orbit-cloud` only for Linux-friendly work (index, tests,
builds), and only after the local load is cleaned up.
