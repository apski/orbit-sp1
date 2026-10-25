---
name: orbit-sp1
description: "Index and init for orbit-SP1: install every subsystem into a React Native repo and bring each one online in order (NavLab: navigation map, DS audit, architecture pack; Orbit: cited code index; Spectra: RN/Figma -> native UI compiler), report what's online, or compare this orbit-SP1 against another Orbit checkout and propose what to pull in. Use when the user says 'install', 'install orbit-SP1', 'init', 'set everything up', 'bring it online', 'what's installed', 'orbit status', 'compare with our orbit', 'pull in what's new'."
---

# orbit-SP1: install, bring online, compare

Find the orbit-SP1 checkout first (it is never copied into repos):

```sh
OP=$( [ -x .orbit/home ] && .orbit/home || for d in "$ORBIT_HOME" ~/code/orbit-sp1; do [ -f "$d/ORBIT-SP1.md" ] && { echo "$d"; break; }; done )
```

If `$OP` is empty, ask the user where orbit-SP1 is cloned (or to clone it to `~/code/orbit-sp1`). Index: `$OP/ORBIT-SP1.md`.
CLI: `$OP/bin/orbit`.
Each subsystem has its own L1 bootstrap skill; this skill runs them in order and keeps the user oriented. Everything
learned goes into the subsystem's files (navlab.config.json, .orbit/orbit.toml, .spectra/profile.json), never only chat.

## "install" (into the current RN repo, or the one the user names)

1. `$OP/bin/orbit install <repo>`: NavLab bootstrap, Orbit shims + census, Spectra install,
   skills into `.agents/skills` (+ `.claude/skills`), AGENTS.md blocks. Writes metadata only, never app code;
   `--only orbit` (or `navlab,orbit`) installs a subset. NavLab is skipped on repos without react-native.
2. Show the status table it prints. Then bring subsystems online **in this order**, one at a time, each through its own
   skill (they ask the user only what their step needs; batch questions into one message per subsystem):

| Order | Subsystem | Skill | Online when |
|---|---|---|---|
| 1 | NavLab | `navlab-install` | profile sections confirmed, navscan adapter resolved, navigation tree checked against the app |
| 2 | Orbit | `orbit-bootstrap` | `.orbit/orbit.toml` written from the census proposal (user approves; starter in `$OP/orbit/examples/`), `.orbit/orbit index` runs, `.orbit/orbit status` lists units |
| 3 | Spectra | `spectra-bootstrap` | profile confirmed, jest harness renders a real screen, one screen passes `spectra verify` |

   NavLab first: its profile (design-system roots, native DS folder) feeds Spectra's. Orbit before Spectra so code
   questions during Spectra's bootstrap can go through `.orbit/orbit` instead of grep.
3. Re-run `$OP/bin/orbit status <repo>` after each one and show it. Stop when all three say
   online, or when the user wants only some (Figma-only work needs just Spectra's figma step).

Subsystem-specific checks after that: DS audit (`ds-audit`), navigation audit (`navlab-audit`), the architecture
skills (`app-architecture-steward`, `rn-native-migration`, ...), Spectra conversions (`spectra`, `spectra-figma`,
`spectra-components`). Each has its own preflight.

## Repo already has its own Orbit (`agents/orbit/bin/orbit`)

That Orbit is the high level. `install` detects it and installs SP1 *into* it: a block in `agents/orbit/ORBIT.md`
(SP1 subsystems as rows of its table), no second engine, and no overwriting of its skills (its `/orbit` stays; kept
names are printed). Skip the `orbit-bootstrap` row above. Then work `$OP/RECONCILE.md` in order: verdicts per
subsystem (SP1 Spectra is the compiler; the host engine wins), Spectra steps S1-S4, the host's two live bugs first,
and the harness rules. Changes to the host's DECISIONS.md, streams and routes are proposals in its own review flow.

## "status"

`$OP/bin/orbit status <repo>`: one line per subsystem with the next step. Report it as is.

## "compare with our orbit" / "pull in what's new" (e.g. on a machine that already has an Orbit)

1. `$OP/bin/orbit compare <their orbit checkout or repo>`: markdown evidence: commands and
   files only on either side, changed files with line counts, and which subsystems (NavLab, Spectra) are new there.
2. Judge with evidence, not by reading diffs alone: run both Orbit test suites; where both have `bench/`, run the same
   benchmark on the same repo; read `orbit/PROVENANCE.md` for what the snapshot is.
3. Propose a merge plan to the user, grouped: take (newer here, with evidence), keep theirs (newer there, or tuned to
   their repo: `.orbit/orbit.toml`, `.orbit/policy.toml`), new subsystems to add. Apply nothing until the user approves;
   their Orbit may be shared with a team.
