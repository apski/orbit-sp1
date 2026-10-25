---
name: orbit-bootstrap
description: Bootstrap or extend Orbit on any codebase (SPEC section 11, phase 0). Runs a deterministic census of the repo (languages, Xcode/RN/web/Python/Rust targets, build tools, tests, CI, design tokens, agent context, skills, CLI tools, churn and hotspots), compares it to what Orbit covers, to a capability catalog and to the repo's product policy (retired areas), and prints a ranked proposal (orbit.toml additions, adapters, check-sets, sources, ref-regex blind spots, unlinked specs, skill routing, missing skills/tools, skills naming dead paths, retire inventories). Use when the user says "bootstrap orbit", "orbit for this repo", "what does orbit not cover", "orbit coverage", "census", "which skills does this codebase need", or before adding Orbit units for a new platform (e.g. mobile).
---

# Orbit bootstrap

The engine is orbit-SP1's `orbit/bootstrap/census.py`, run through the repo's `.orbit/census` launcher: stdlib only, Python 3.13 or newer, read-only git, no network, no model. It reads two data files:
- `orbit/bootstrap/catalog.toml` (in orbit-SP1) is generic. It maps stack signals to adapters, checks, sources, tools and skills.
- A per-repo policy holds product decisions: `.orbit/policy.toml`.

The contract it serves is `$(.orbit/home)/orbit/SPEC.md` section 11, with sections 4, 6 and 10 behind it. Its JSON fields are documented in [references/schema.md](references/schema.md).

**Never apply a proposal without the human's approval, item by item.** The census only proposes. You may edit `orbit.toml`, add an adapter, or write a check only after the person has said yes to that item.

## Run

```sh
C=.orbit/census          # the launcher picks Python 3.13+ and passes this repo; a later --repo overrides it
$C census                     # markdown summary; --json for the schema
$C propose --top 30           # coverage table, ranked items (Mobile first, then all), routing table
$C propose --json > /tmp/orbit-before.json
$C report --top 30            # census + proposal as one document
```

- To census another repo, pass `--repo <path>`. With no `orbit.toml` in that repo, everything is a gap, so the first approved item becomes writing one.
- `--since YYYY-MM-DD` sets the churn window. The default is the policy's `since`, else the HEAD commit date minus 90 days, so output never depends on the clock.
- `--policy FILE` overrides the default policy, the first existing path in catalog `[census].policy_files`. A policy holds:
  - `[policy] retire = ["path"]` with `[policy.why]`: a retired area gets one `retire` item (an inventory for removal) and no integrate items;
  - `since`;
  - `demand_module` (it reuses a demand script's `PATH` buckets) or inline `[policy.demand]`.

  Record product decisions there, with who decided and when, instead of arguing with the churn numbers.
- **Frozen app targets.** An Xcode, React Native or Android target with 0 commits in the window is frozen: it gets one `retire` item (an inventory: files, last commit, workspace deps to keep and prune, external bundle ids, inbound references) and no adapter, check or skill items. Confirm a frozen target with the person, then record the decision in the policy.
- `--exclude GLOB` (repeatable) drops scratch copies or vendored trees that the catalog excludes do not already cover. Read the "Churn by top-level area" table first: an area that duplicates real code (a `tmp/`, a copied worktree) inflates every count. Record the exact command you ran.
- `--skill-root DIR` (repeatable) replaces the catalog's roots: `.agents/skills`, `.claude/skills`, `~/.claude/skills` and `~/.codex/skills`. Skill and tool results depend on the machine (skill roots and PATH); everything else depends only on the tree, HEAD, catalog and policy.

## Workflow

1. **Census.** Run `report`, then check the target roots and signals against what you know of the repo.
   - A wrong domain or a missed stack is a catalog bug. Fix `catalog.toml` (data, not code), then rerun.
   - Any catalog change moves the goldens (see "Maintain").
2. **Review with the human.** Show the coverage table and the top ranked items, mobile first if mobile is in scope. Each item has a `why` line with `path:count` evidence and a literal `change`.
   - Ask which items to approve.
   - Raise items that look wrong, for example a unit glob that should be split. Do not quietly drop them.
   - `score = (commits + 1) x gap / scope`: churn times the share of the item's scope that it fixes.
   - Product decisions belong in the policy file, not in hand-reordering. Example: an app being retired.
   - `skill-ref` and `tool` items are cheap to fix and unblock agents. Offer them first.
3. **Apply approved config only.** Edit `orbit.toml` for the approved `include`, `unit` and `language` items, and `exclude_units` where an item says so.
   - Order matters: Orbit's first matching unit wins, so put specific kinds before broad ones.
   - Do not touch items that were not approved.
4. **Build in SPEC section 11 order.** Each step has its done-check; do not skip ahead.
   1. Config: `orbit status` lists the new units.
   2. Language adapters: a new `[languages.X]` block plus a golden fixture, with 95% or more of files parsing `ok`.
   3. Index: `orbit index` run twice gives the same digest, and the second run parses 0 files.
   4. observe
   5. Routes: words for the new platform in `routes.toml`, plus labelled questions in `tests/questions.toml`.
   6. Artifacts and principles.
   7. Packet.
   8. Verify: wire the approved `check` items as `[checks.*]` plus a `[check_sets]` entry. A missing tool reports `unavailable`, never `passed`.
   - `adapter` and `source` items are code behind Orbit's fixed interfaces (SPEC 4.1, 4.3). Each needs a golden test (R12).
   - Do not start analyses (step 11) before the index golden test is green.
5. **Confirm.** Rerun `propose --json > /tmp/orbit-after.json` and compare the two `coverage` arrays:
   ```sh
   python3 -c "import json,sys;b,a=(json.load(open(f))['coverage'] for f in sys.argv[1:]);[print(x['lang'],x['covered_files'],'->',y['covered_files'],'/',y['files']) for x,y in zip(b,a)]" /tmp/orbit-before.json /tmp/orbit-after.json
   ```
   - Covered counts must go up for every language you touched, and the applied items must drop off the list.
   - Then run `.orbit/engine-tests` and `.orbit/orbit verify fast`.
6. **Record.** Write or refresh `docs/orbit/<area>/BOOTSTRAP-<REPO>.md` with the `report` output and the exact command. Add one line per approved and rejected item, saying who decided.

## Skills to load per task

The `routing` table in the proposal comes from `[tasks.*]` in the catalog, filtered to the signals this repo has. It lists the skills to load for each task type and marks any that are missing. A MISSING skill item says either how to install it (for example, the argent skills come from `argent init -y`) or that it has to be built. Building a skill is a separate approved task, done with `skill-creator`.

## Maintain

- New stack: add a `[[signal]]` row to `catalog.toml`, plus `[adapters.*]`, `[checks.*]` and `[sources.*]` rows if it needs them, and route it with `[tasks.*]`. Skill names must be real; check them against the skill roots' `SKILL.md` frontmatter.
- Tests (in the orbit-SP1 checkout): `python3 -m unittest discover -s orbit/bootstrap/tests`. The fixture is a tiny Swift + RN + Python repo, committed with pinned dates. A changed output needs `UPDATE_GOLDEN=1` and one sentence in the commit saying why.
- Known limits:
  - `check` presence is a regex match plus a substring match on the root path.
  - `unit` candidates come from directory names in `[census].unit_parents` or from sibling Cargo crates.
  - `refs` use counts are textual, so a name like `API` also counts prose.
  - `skill-ref` skips runtime paths (`/tmp`, `/etc`).
  - Line counts are newline counts.
