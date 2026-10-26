# orbit-SP1

Orbit plus service pack 1: everything an agent needs to work on a React Native app and move it toward native, installed
with one command and brought online by one skill. Deterministic code first; agents only where judgement is needed, and
their output is checked by code.

```bash
bin/orbit install <repo> [--only navlab,orbit,spectra,orbit-rag,voyager,perf,gate]   # every subsystem (metadata only, no app code), then status
bin/orbit status [repo]                                   # what's online, the next step per subsystem
bin/orbit compare <other-orbit>                           # evidence for merging with another Orbit checkout
bin/orbit sync [--check] [--install-hooks]                # user level from this checkout: rules, argent-sim, skills (+ git hooks that re-run it)
bin/orbit cloud run [--on auto|local|<host>|do] -- <cmd>   # route a job: macOS work here, the rest to a Linux host (orbit-route skill)
bin/orbit <engine command>                                # index, how, refs, packet, verify, perf, ... (nearest .orbit/)
```

Put `bin/` on PATH (or `alias orbit=<checkout>/bin/orbit`). Then in the repo, ask the agent to run the **`orbit-sp1`**
skill: it runs each subsystem's L1 bootstrap in order and reports status.

| Subsystem | Dir | What it does | L1 skill |
|---|---|---|---|
| Orbit | `orbit/` | cited code index: index, refs, observe, packets, principles, verify, perf; census bootstrap | `orbit-bootstrap` |
| NavLab | `navlab/` | navigation map (navscan), repo profile, DS audit, architecture pack (React Native repos) | `navlab-install` |
| Spectra | `spectra/` | UI compiler: RN (jest or running app) / Figma (REST) -> IR -> SwiftUI on FlexLayout (a Swift port of RN's Yoga) -> judged against the source's own layout | `spectra-bootstrap` |
| Perf | `perf/skills/` | perf skill pack (thread-offload, perf-climb, ...) | per skill |
| Voyager-1 | `voyager/` | the layer above Orbit: job -> validated draft PR. Protected validator (fail-to-pass, held-out scenarios, mutants, no weakened tests), diff-only reviewer, argent recording for UI jobs, resumable state machine; strengthening loop + constraint maps; one-shot vs factory backtest | `voyager` |
| Gate | `gate/` | the agent gate for RN repos: LLM-free oracle (typecheck, contracts diff, fitness, simwalk flows) as Orbit check sets `gate` / `gate-device`, a ratchet (no ruler looser than the base commit) and a Claude Code Stop hook that blocks on red; loop in `.orbit/CLIMB.md` | `gate` |
| Cloud | `bin/cloud.mjs` | where a command runs: `local` (this Mac), a host you own (`~/.orbit-cloud/hosts.json`) or `do` (a throwaway DigitalOcean droplet: ssh from this IP only, TTL, reaped). Each is set up with zsh + oh-my-zsh, node, python, claude, codex and the pack at this checkout's HEAD + working tree. `--on auto` keeps macOS work local and sends the rest out; `--detach` runs streams in tmux | `orbit-cloud`, `orbit-route` |
| orbitfleet | `orbitfleet/` | Go: leases slim iOS simulators to agents (one agent, one sim), runs/marks/shots, local UI on 127.0.0.1. Built and linked by `bin/orbit sync`; contract in its skill | `orbitfleet` |
| simwalk | `simwalk/` | LLM-free simulator journey runner on argent's native binaries: `simwalk flows/<name>.mjs --repeat 3` (format: `simwalk/FLOWS.md`); Gate's oracle runs it | none |
| orbit-rag | `orbit-rag/` | docs-and-decisions RAG (Rust): hybrid search, cited bundles with human-approved highlights first, read-only MCP `orbit-rag`, code model, drift check; search UI as a login service on 127.0.0.1:7420 (`--port n`) | none: `install` brings it online |

`orbit-rag/` has its own README
(index in `<repo>/.orbit-rag/`, separate from Orbit's `.orbit/`).

Long tasks follow HARNESS.md (gate, ratchet, dev log, owned files, escalation).

## Every subsystem has an installer

A subsystem isn't part of the pack until `bin/orbit install --only <name> <repo>` puts it into a repo and
`bin/orbit status` reports it. New subsystems are made installable with the **`orbit-installer`** skill. The contract,
checked by `bin/orbit installer-check <name> <fixture repo>`:

1. **One entry point**: `<subsystem> install <repo>` (or a step in `bin/orbit install`), exit non-zero on failure,
   last line `<name>  <state>  <note>`.
2. **Metadata only**: its own dot-dir / config files, a launcher, skills, a marked `AGENTS.md` block. Never the
   repo's own files.
3. **Idempotent**: a second install changes nothing; a profile the user edited is never overwritten.
4. **Nothing machine-specific in the repo**: launchers find this checkout at run time (`$ORBIT_HOME`, else
   `~/code/orbit-sp1`); machine-only settings live outside the repo (e.g. `~/.voyager/<name>/local.toml`).
5. **An adapter, not a fork**: per-repo facts in one committable profile, starters in `<subsystem>/examples/`,
   detection for repos without one.
6. **Status**: one row `<name>  not installed|installed|online  next: <step>`; the L1 skill brings it online.
7. **Registered**: a step in `install()` and a row in `status()` in `bin/orbit.mjs`, its skills root in
   `skills-sync`, a row in the table above.

| Subsystem | Installer | installer-check |
|---|---|---|
| Orbit | `bin/orbit install` (launchers + census) | PASS (python fixture) |
| Voyager-1 | `voyager install` | PASS (python fixture) |
| Perf | `bin/orbit install` (skills) | PASS (python fixture) |
| NavLab | `navlab/bootstrap.mjs` | not run yet: needs an RN fixture repo |
| Spectra | `spectra install` | not run yet: needs an RN fixture repo |
| Gate | `gate install` (RN repos) | PASS (`gate/tests/rn-fixture`) |
| Cloud | none: machine-level, writes nothing into a repo (its skill comes with the pack skills) | n/a; `node --test bin/cloud.test.mjs`, DO adapter proved on a live droplet 2026-10-26 |
| orbit-rag | `orbit-rag/install.mjs` | not run yet: registers an MCP server + a LaunchAgent (machine-global), needs a `--dry` mode first |

**Repo with its own Orbit** (`agents/orbit/`): SP1 installs into it (a block in its `ORBIT.md`, no second engine,
its skills kept), then `RECONCILE.md` says which side wins per subsystem.

**Per-repo state after install:** `.orbit/` (Orbit config, house rules, routes, policy, index and run log, plus the
launchers `orbit`, `census`, `engine-tests`, `home`), `.spectra/` (profile, jest harness, Figma cache, generated code,
preview app), `navlab.config.json` + `navlab.adapter.json`, skills in `.agents/skills` (and `.claude/skills`, linked
or copied to match the repo), and marked blocks in `AGENTS.md`.

**Nothing machine-specific goes into a repo.** Launchers find this checkout at run time: `$ORBIT_HOME`, else
`~/code/orbit-sp1`. Skills refer to it as `$(.orbit/home)`. A repo can commit `.orbit/`
(minus its gitignored index and runs) and work on any machine that has the pack.

**Requirements:** node 22 (22.15+ for the zstd Figma cache), python 3.13+ (Orbit, NavLab arch), git, Xcode + xcodegen
(Spectra verify), a booted iPhone simulator; cargo (orbit-rag build) and, optionally, ollama (orbit-rag semantic search). Figma: `FIGMA_TOKEN` in `.spectra/secrets.env` or `~/.spectra/secrets.env`.

**Provenance and limits:** `orbitfleet/` and `simwalk/` were moved in from their own repos on 2026-10-26 (orbitfleet `7393381` plus its uncommitted tree, simwalk `0db68ab`). `orbit/PROVENANCE.md` (engine extraction, perf merge), `navlab/MANIFEST.sha256` (NavLab
snapshot), `spectra/README.md` (what Spectra doesn't do yet).

## New machine, or pulling an update

The order an agent follows when told "pull orbit-SP1 and integrate it":

1. `git clone git@github.com:apski/orbit-sp1.git ~/code/orbit-sp1`, or `git -C ~/code/orbit-sp1 pull --ff-only`.
2. Tools first (sync patches argent's `argent.md` only if argent is installed). Tools the pack calls but doesn't ship (check with `command -v`; each is optional and its rule falls back without it):
   node, python3.13+, git (required); cargo + npm (orbit-rag only); go (builds `orbitfleet`); `argent` (`npx @swmansion/argent@latest init -y`); `~/.local/bin` on PATH.
   orbitfleet and simwalk ship in the pack; sync builds and links them.
3. `bin/orbit sync --install-hooks`: rules into `~/.claude/rules` (and step 0 into argent's `argent.md`), `argent-sim`,
   `orbitfleet` (built with go) and `simwalk` into `~/.local/bin`, every skill into `~/.claude/skills`, and git hooks so later pulls re-sync by themselves.
   Rows saying `edited in copy` / `unmanaged copy differs` were kept: diff them against the pack and ask before `--force`.
4. Repos that already have the pack (`ls -d ~/code/*/.orbit`): `bin/orbit install <repo>` again (idempotent; rewrites
   pack-owned files only), then `bin/orbit status <repo>`.
5. Done when `bin/orbit sync --check` exits 0.

## Moving this checkout

Launchers look in `$ORBIT_HOME`, then `~/code/orbit-sp1`. If you keep it elsewhere, set `ORBIT_HOME`. After moving it,
re-run `bin/orbit sync` (global copies carry the NavLab path) and `bin/orbit install <repo>` on installed repos.
