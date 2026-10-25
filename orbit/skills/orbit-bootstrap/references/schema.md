# Census and proposal JSON (schema version 2)

Both are printed by `orbit/bootstrap/census.py` with `--json`, `sort_keys` and a 2-space indent.
- Every list is sorted by a stable key.
- There are no timestamps and no absolute paths. Paths are repo-relative, and skill roots keep the label they were given (`~/.claude/skills`), not the expanded home directory.
- A breaking change bumps the version in `schema`. Version 2 added policy, hotspots, demand, tools, skill path checks, `targets[].last_commit`/`retired`/`tree`/`test_bundles`/`flows`/`workspace_deps`, `scope_files`, frozen targets, and the item kinds `refs`, `spec`, `tool`, `skill-ref` and `retire` (with `inventory`).

## Terms

- **target root.** A directory that a `platform` signal with `root_up` marks as buildable. Examples: the parent of `X.xcodeproj`, or the directory holding `package.json`, `Cargo.toml`, `Pipfile` or `settings.gradle`.
- **owner.** The deepest target root that contains a file. A root at `.` is transparent: a file with no deeper root is owned by its top-level directory, and a file at the top level is owned by `.`.
- **signal.** A `[[signal]]` row in `catalog.toml`. It matches files by glob, with two optional filters:
  - a `contains` regex over the file text;
  - a `json_dep` list, matched against the `dependencies`, `devDependencies` and `peerDependencies` of a `package.json`.
- **policy.** Per-repo product decisions in TOML, found at the first path in `[census].policy_files` or given with `--policy`.
  - `[policy] retire = [paths]`
  - `[policy.why] "<path>" = "reason"`
  - `since = "YYYY-MM-DD"` overrides the churn window.
  - `demand_module = "path/to/demand.py"` reuses that script's `PATH` dict of bucket → regex.
  - `[policy.demand]` adds inline buckets.
- **machine-dependent.** Only two parts of the output depend on the machine: `skills` (it reads the skill roots) and `tools` (it looks up PATH). Everything else depends only on the tree, HEAD, catalog and policy.

## `census --json` (`schema: "orbit-census/2"`)

| Field | Type | Meaning |
|---|---|---|
| `repo.name` | str | The repo directory's name. |
| `repo.head`, `repo.head_date` | str or null | The HEAD sha and its committer date (`YYYY-MM-DD`). |
| `repo.since` | str | Start of the churn window: `--since`, else the policy's `since`, else `head_date` minus `[census].churn_days`. |
| `repo.commits` | int | Commits since `since` that touch at least one non-excluded path. |
| `repo.files` | int | Tracked plus untracked files that are not git-ignored or catalog-excluded. Regular files only. |
| `repo.large_files` | int | Language files over `max_bytes`. They count as files but contribute 0 lines. |
| `policy` | obj or null | `{file, retire[], why{}, since, demand_module}`. |
| `languages[]` | `{lang, code, files, lines}` | Sorted by lines (descending), then by name. |
| `targets[]` | `{root, kinds[], domain, files, code_files, lines, langs{}, commits, last_commit, signals[], retired, schemes[]?, tree[], test_bundles[], flows[], workspace_deps[]}` | One row per target root other than `.`; the parts are described below the table. |
| `signals[]` | `{id, category, domain, files, groups[{root, files}], paths[]}` | Detected signals in catalog order. `paths` holds the first 10 matches. |
| `areas[]` | `{path, files, code_files, lines, commits}` | One row per top-level directory, plus `.`. |
| `hotspots[]` | `{path, commits}` | Directories up to 4 levels deep, ranked by how many commits in the window touched a code file in them. Shows `[census].hotspots` rows. |
| `demand[]` | `{bucket, regex, commits}` | Policy buckets: commits in the window with any changed path matching the regex. This is how `demand.py` counts paths. |
| `skills.roots[]` | `{root, exists, skills}` | One row per skill root scanned. |
| `skills.found[]` | `{name, roots[], path, description, missing_refs[], tools[]}` | `missing_refs` and `tools` are described below. |
| `tools[]` | `{id, any[], found, always, signals[], skills[]}` | `found` is the first name in `any` that is on PATH, or null. |

Parts of a `targets[]` row:
- `commits` counts commits in the window. A target whose `kinds` intersect `[census].freeze_kinds` and whose `commits` is 0 is **frozen**.
- `last_commit` is the newest commit touching the root, over all history.
- `retired` is true when the root sits under a policy retire path.
- `tree` holds `[{path, code_files, lines, commits}]` for directories up to 3 levels below the root that hold code, sorted by path. `commits` counts the window's commits touching any file under the directory.
- `test_bundles` lists the shallowest directories below the root whose name ends in `Tests` (so `*UITests` too) and that hold code.
- `flows` holds `[{dir, count}]`, with `dir` being each `.argent/flows` directory under the root and `count` its yaml files.
- `workspace_deps` lists other target roots this one depends on: `package.json` dependencies whose name is another target's `package.json` name, or Cargo `path =` dependencies.

`skills.found[].missing_refs` lists paths the skill names that do not exist:
- Home paths (`~/...`, `/Users/...`) are checked for every skill.
- Repo paths, meaning a first component that is a top-level directory of the repo, are checked only for skills inside the repo, and so are bare `` `File.swift` `` names that match no file in the repo or the skill directory.
- Not checked: `/tmp`, `/etc` and paths on remote boxes, plus anything templated (`*`, `<>`, `{}`, `...`, `XXX`, `MyApp`).

`skills.found[].tools` lists the catalog tool names the skill calls inside code spans.

## `propose --json` (`schema: "orbit-proposal/2"`)

| Field | Type | Meaning |
|---|---|---|
| `repo`, `policy` | obj | Same as in the census. |
| `orbit` | obj or null | The orbit.toml that was compared: `config`, `include`, `ignore`, `units[]`, `exclude_units`, `languages`, `checks{id: command}`, `check_sets`, `sources`, `specs`. |
| `coverage[]` | `{lang, files, covered_files, lines, covered_lines, adapter}` | Code languages only, with retired paths excluded. A file is covered when Orbit's include matches it, its ignore does not, and some Orbit `[languages]` block lists the file's extension. |
| `items[]` | see below | The ranked proposal. |
| `routing[]` | `{task, signals[], skills[{name, present}]}` | Task types whose `when` signals are active, meaning detected at a root that is not retired. |

### `items[]`

`{rank, kind, id, domain, score, commits, gap_files, scope_files, gap_lines, why, evidence[], change}`

- `score = round((commits + 1) * gap_files / scope_files, 2)`: churn times the share of the item's scope that it fixes.
- Ties are sorted by `gap_files` (descending), then by kind order, then by id.
- `commits` counts the window's commits that touched the item's churn files. By default those are its gap files; some kinds use other files, as the table says.
- Retired paths never appear in an integrate item. They get a single `retire` item instead.

| kind | id | gap / scope | Emitted when |
|---|---|---|---|
| `include` | `<root>/**` | Uncovered code files with an Orbit language / all code files of the root | Orbit has a language block for the files but does not include them. |
| `unit` | `<dir>/*` | Code files in no Orbit unit / all code files in the children | Either (a) a dir named in `unit_parents` has 2 or more children with code, or (b) 2 or more sibling roots of a signal with `unit_kind` (Cargo crates) exist. Never proposed for an existing unit, or for a dir inside one. `change` adds `exclude_units` for any unit kind nested under the dir. |
| `language` | lang | Files no Orbit block parses / files of that language | At least `min_lines` lines are unparsed. `change` also gives include globs. |
| `refs` | Orbit language | Files using a blind name / files of that language | A declared TitleCase type name never fully matches that language's `ref` regex, yet the code uses it more often than it declares it. Example: SKD. |
| `spec` | doc dir | Unlinked docs / docs in the dir (churn: files of the units they match) | A doc matching `spec_globs` is cited by no orbit `[[spec]]`. `change` writes `[[spec]]` lines for docs whose stem matches a unit name. |
| `adapter` | catalog adapter | Uncovered / all files in its languages in the wanting roots | Coverage is under 90%. |
| `check` | `<check>:<root>` | All code files of the root | No Orbit check command both matches the catalog regex and names the root. The command template fills `{root}`, `{name}` (the dir name) and `{workspace}` (the outermost ancestor root of the same signal). `change` adds the check to a `[check_sets]` entry named after the domain. |
| `source` | catalog source | Code files in the wanting roots | orbit.toml has no `[sources.<id>]`. |
| `tool` | catalog tool | Code files in scope | No name in `any` is on PATH, and the tool is needed: it is `always`, or a signal lists it, or a skill calls it. |
| `skill` | skill name | Code files in the wanting roots | The skill is in no scanned root (MISSING). |
| `skill-ref` | skill name | Missing paths (churn: the SKILL.md, if it is in the repo) | The skill names paths that do not exist. |
| `retire` | policy path or frozen target root | Code files under the path | Emitted once per retired area, either because the policy retires the path or because the target is frozen. A frozen or retired root nested under another retire root folds into it. The item carries an extra `inventory` object, described below the table. |

The `inventory` object on a `retire` item is `{root, files, code_files, lines, last_commit, workspace_deps[{root, also_used_by[], action}], external_ids[{id, also_used_by[], action}], inbound_refs[], nested_targets[]}`.
- `workspace_deps`: a sibling package that a live target also uses is marked `keep, prune`; otherwise it is marked `remove`.
- `external_ids` are bundle ids and packages taken from pbxproj, gradle and app.json files. An id that a live target also uses is marked keep, so its App Store record must not be retired.
- `inbound_refs` lists the files outside the area that mention it.

A signal with a `domain` only counts at roots of that domain. For example, `react-dom` in a backend's `package.json` does not create web-adapter work.

## Consumers

- `docs/orbit/mobile/ORBIT-MOBILE-DESIGN.md` phase 0 reads `targets[]` (roots, schemes, domain, last_commit), `coverage[]`, `hotspots[]`/`demand[]`, and the `mobile` items.
- After approved items are applied, run `propose --json` again and compare the `coverage[]` rows. The covered counts should go up, and the applied items should drop out of the list.
