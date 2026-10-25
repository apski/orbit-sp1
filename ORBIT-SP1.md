# orbit-SP1

Orbit plus service pack 1: everything an agent needs to work on a React Native app and move it toward native, installed
with one command and brought online by one skill. Deterministic code first; agents only where judgement is needed, and
their output is checked by code.

```bash
bin/orbit install <repo> [--only navlab,orbit,spectra,orbit-rag]   # every subsystem (metadata only, no app code), then status
bin/orbit status [repo]                                   # what's online, the next step per subsystem
bin/orbit compare <other-orbit>                           # evidence for merging with another Orbit checkout
bin/orbit skills-sync [--check]                           # this pack's skills -> your global skills dir, as real files
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
| orbit-rag | `orbit-rag/` | docs-and-decisions RAG (Rust): hybrid search, cited bundles with human-approved highlights first, read-only MCP `orbit-rag`, code model, drift check; search UI as a login service on 127.0.0.1:7420 (`--port n`) | none: `install` brings it online |

`orbit-rag/` has its own README
(index in `<repo>/.orbit-rag/`, separate from Orbit's `.orbit/`).

Long tasks follow HARNESS.md (gate, ratchet, dev log, owned files, escalation).

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

**Provenance and limits:** `orbit/PROVENANCE.md` (engine extraction, perf merge), `navlab/MANIFEST.sha256` (NavLab
snapshot), `spectra/README.md` (what Spectra doesn't do yet).

## Moving this checkout

Launchers look in `$ORBIT_HOME`, then `~/code/orbit-sp1`. If you keep it elsewhere, set `ORBIT_HOME`. After moving it,
re-run `bin/orbit skills-sync` (global copies carry the NavLab path) and `bin/orbit install <repo>` on installed repos.
