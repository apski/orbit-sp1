---
name: orbit-installer
description: "Make a new orbit-SP1 subsystem installable: give it an installer that meets the pack's installer contract (one entry point, metadata only, idempotent, nothing machine-specific, an adapter profile, a status row, registered in bin/orbit), then prove it with `bin/orbit installer-check`. Use when the user says 'new subsystem', 'make X installable', 'add an installer', 'installer for', 'register in orbit install', 'it should install itself into the repo', or when a subsystem exists in the pack without an install step or a status row."
---

# orbit-installer: every subsystem installs itself

The contract is in `$(.orbit/home)/ORBIT-SP1.md` ("Every subsystem has an installer"). The reference
implementations to copy from: `voyager/voyager.py` (`install`, `detect`, `status`, `LAUNCHER`) for a Python subsystem,
`orbit-rag/install.mjs` for one with its own build step.

## Steps

1. **Decide what lands in a repo.** List every file the subsystem needs there: its dot-dir (`.<name>/`), one
   committable profile (the adapter), a launcher, skills, an `AGENTS.md` line. Anything machine-specific (paths
   to venvs on this Mac, DSNs, tokens) goes in `~/.<name>/<repo>/local.toml` or env, never in the repo.
2. **Write the adapter starter + detection.** `<subsystem>/examples/<repo-name>.toml` for repos you know;
   `detect(repo)` for the rest: read what the repo already has (test runner, package manager, dirs) and write a
   commented starter. Never overwrite an existing profile.
3. **Write `install <repo>` and `status <repo>`.**
   - install: create the files above, print one last line `<name>  <state>  <note>`, exit non-zero on failure.
   - status: one line `<name>  not installed|installed|online  next: <the step that brings it online>`.
   - The launcher finds the pack at run time (copy `LAUNCHER` from voyager.py: `$ORBIT_HOME`, else `~/code/orbit-sp1`).
4. **Register it in `bin/orbit.mjs`**: a numbered step in `install()` (call the installer, `placeSkills` its skills
   dir, push one `log` row), a row in `status()`, its `skills` root in `skillsSync()`, the default `only` list,
   the `AGENTS.md` block sentence. Add its row to the tables in `ORBIT-SP1.md`.
5. **Prove it.** Make the smallest fixture repo the installer applies to (a dir with a test, a package.json, ...):
   `bin/orbit installer-check <name> <fixture>` must print six PASS lines (exits twice, idempotent, nothing
   machine-specific, status row shape, wrote something, never touched the repo's own files). Fix and rerun until it
   does. If the installer has machine-global side effects (MCP registration, LaunchAgents), give it a `--dry` /
   `--no-service` mode first and say so in the ORBIT-SP1.md row; don't run it against a temp repo without that.
6. **The L1 skill** (`<name>-bootstrap` or a section of the subsystem's main skill) takes it from `installed` to
   `online`: what the user confirms in the profile, the first command that proves it works.

Report the installer-check table and the status row. A subsystem without both is not done.
