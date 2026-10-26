---
name: orbit-cloud
description: "Run pack work somewhere other than this Mac with `orbit cloud`: targets `local` (this Mac), a host you own by ssh (linux-box, from ~/.orbit-cloud/hosts.json) and `do` (a throwaway DigitalOcean Linux droplet), each set up with zsh + oh-my-zsh, node 22+, python 3.13+, uv, claude, codex and the orbit-sp1 pack. `--on auto` routes by command, `--detach` runs long streams in tmux, and adding or setting up a host, syncing, logs, logins and teardown are covered too. Which target a job should use: the orbit-route skill. Use when the user says 'orbit cloud', 'run it on a box', 'run on linux-box', 'offload to DO', 'spin up a droplet', 'cloud box', 'add a host', 'set up the linux box', 'detached job', 'job logs', 'the Mac is overloaded', 'run the tests remotely', or when this Mac's load is far above its core count and the work is Linux-friendly (index, tests, builds, Voyager)."
---

# orbit-cloud: where a command runs

`orbit cloud ...` (`$(.orbit/home)/bin/cloud.mjs`; self-test `node --test bin/cloud.test.mjs`). Which target to pick
for a job: skill `orbit-route`. This skill is the tool itself.

## Targets (`--on`)

| `--on` | What | Use for |
|---|---|---|
| `local` | this Mac, in place; warns when load > 2x cores | anything; the only target with simulators, Xcode, Instruments, argent |
| `<host>` e.g. `linux-box` | a machine you own, over ssh, persistent and free | Linux work by default: tests, builds, index, Voyager, streams |
| `auto` | macOS tools in the command (xcrun, simctl, xcodebuild, pod, swift, argent, simwalk, orbitfleet, `run:ios`, `spectra verify`, ...) -> local, else the first reachable host, else local. Never DO. Prints its choice | the default to reach for |
| `do` | droplet `orbit-cloud-<name>`: Ubuntu 26.04, lon1, s-4vcpu-8gb ($0.07/h), TTL | a burst wider than the host; costs money |

Defaults when `--on` is left out: `run` -> local; `up`, `ssh`, `sync` -> do; `jobs`, `logs`, `attach` -> auto.

## Commands

```sh
orbit cloud run --on auto -- 'npm ci && npm test'        # sync this repo up, run in the same subdir, stream output, exit code
orbit cloud run --on auto --pull out/report.json -- <cmd> # ...then copy paths back into the repo here
orbit cloud run --on auto --detach -- <long cmd>         # tmux on the target, returns an id at once
orbit cloud jobs --on linux-box                        # last 20: id, running | [exit n] | died, command
orbit cloud logs <id> --on linux-box [-f]              # tail (follow) a job's log
orbit cloud attach <id> --on linux-box                 # the job's tmux live (detach: C-b d)
orbit cloud up --on linux-box | --on do [--name n] [--size s-8vcpu-16gb] [--ttl 8h]   # set up (DO: create first)
orbit cloud sync --on <t>                                # just push this repo
orbit cloud ssh --on <t>                                 # zsh there, in the work dir
orbit cloud ls                                           # hosts (reachable via, load, job logs) + DO boxes ($/h, expiry)
orbit cloud down [--name n | --all]                      # destroy DO boxes; hosts persist
```

One argument after `--` is a shell string (`'a && b'`). Several are argv, each quoted.

## Add a host

1. An ssh alias in `~/.ssh/config` that works with `BatchMode=yes` (key auth). For a box behind NAT, add a second
   alias through a jump host (e.g. `linux-box` on the LAN, `linux-box-remote` through a VPN jump).
   Outbound only: never open a port for this (scanner rule).
2. A row in `~/.orbit-cloud/hosts.json`. It is machine-local and never goes in a repo:
   ```json
   { "linux-box": { "ssh": ["linux-box", "linux-box-remote"], "work": "code/orbit-cloud", "dev": "code/orbit-sp1" } }
   ```
   - `ssh`: aliases tried in order; the first that answers wins.
   - `work`: dir under the remote home for synced repos and the pack mirror. Put it on the big disk
     (e.g. `code` symlinked to a data disk).
   - `dev` (optional): a separate checkout to improve the pack in.
3. `orbit cloud up --on <host>`. The user needs sudo on the host (Ubuntu): apt for zsh, git, tmux, build tools; uv
   adds Python 3.13 if the system's is older. It never changes the login shell on a host. Then `orbit cloud ls`.

## What a target gets (`bin/cloud-setup.sh`, idempotent, re-run on every `up`)

- **Tools:** zsh + oh-my-zsh (root's login shell only on a DO box), git, rsync, jq, ripgrep, tmux, build-essential,
  node 22+, python 3.13+, uv, `claude`, `codex`. The prompt shows the hostname in magenta.
- **`~/<work>/orbit-sp1`, the mirror:** cloned from origin (this Mac's agent forwarded for that one command, no key
  left behind), moved to this checkout's HEAD over ssh (local commits may be unpushed), then this working tree is
  copied over. `git status` there matches here. `orbit sync` runs there. Every `up` resets it, so never edit it.
- **`~/<dev>`, the dev checkout** (hosts with `dev` only): created once at this Mac's HEAD with upstream origin/main,
  then never touched by `up`. Agents improve the pack there on a branch. This Mac gets a git remote named after the
  host: `git fetch linux-box`.
- **Repos:** each lands at `~/<work>/<repo>`. Only that dir is written, so a sync's `--delete` can't reach anything
  else on the host.

## Sync rules

What git sees (`.gitignore` applied), minus `.git` and secrets: `.env*`, `*.pem`, `*.key`, `*.p8`, `*.p12`, `*.crt`,
`*.mobileprovision`, `certs/`, `secrets.env` (Voyager's list). Excluded paths survive on the target, so its own
`node_modules` / `.venv` / build caches persist across syncs. Never copy a secret over by hand. A job that needs
credentials, a prod DB or this Mac's logins stays local.

## Logins (the user's step; `up` reports both)

- **claude:** `orbit cloud ssh --on <t>`, run `claude`, then `/login`.
- **codex:** `codex login --device-auth` on the target prints a URL and a one-time code. Run it there in the
  background and pass the code to the user. Gotcha: the ChatGPT account must first have **"Enable device code
  sign-in for Codex"** turned on in its settings, or the code never completes. Check with `codex login status`
  ("Logged in using ChatGPT").
- **Headless use:** `claude -p "<task>"` and `codex exec "<task>"`, usually as `run --detach` (see `orbit-route`).

## DO specifics

- **Cost:** every box has a TTL (default 2h, as an `orbit-cloud-exp-<epoch>` tag). Every cloud command first destroys
  expired boxes, and `run` extends a box with less than 15 min left. A detached job dies with its box at expiry, so
  pass `--ttl 8h` to `up` for long ones. A box nobody touches keeps billing until the next cloud command, so `down`
  when done. `orbit status` shows live boxes.
- **Network:** DO firewall `orbit-cloud` allows ssh from this machine's current public IP only. It applies by tag
  before boot and is re-pointed when the IP changes (VPN, new network).
- **Safety:** only droplets named `orbit-cloud-*` that also carry the `orbit-cloud` tag are ever touched. Your other
  droplets never are. Don't work around that guard.

## Troubleshooting

- **`<host>: unreachable via a, b`:** off the home LAN the LAN alias times out and the jump alias should answer.
  Check `ssh -o BatchMode=yes <alias> true`. If both fail, the host is off or the jump box is down.
- **Job shows `died`:** its tmux session ended without writing `[exit n]`, e.g. the host rebooted or someone killed
  tmux. Read `logs <id>` for how far it got.
- **`git: detected dubious ownership` on a target:** files were rsynced with this Mac's uid. The tool now passes
  `--no-owner --no-group`; on an old target, run `up` again (or chown the work dir).
- **Killing a remote process by name:** `ssh t 'pkill -f "codex login"; ...'` kills its own shell too, because the
  pattern matches the command line. Use `pgrep` first, or `pkill -f "[c]odex login"`.
- **Numbers:** say where each was measured. The loaded Mac gives noisy wall times; a Linux host gives Linux numbers,
  not iOS ones (HARNESS rule 2).

## Measured (2026-10-26)

Orbit engine tests (43):

| Where | Time |
|---|---|
| this Mac at load 949 | 170 s wall (8.6 s CPU) |
| DO s-4vcpu-8gb | 21.8 s |
| linux-box | 5.3 s; 29 s end to end through the remote jump |

Setup: a new DO box takes 3 min 44 s, a second `up` 25 s. linux-box's first `up` took 1 min 45 s.
