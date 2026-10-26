# Hill-climbing this app

One number goes down, nothing else is allowed to break, and every step is locked in. Written by orbit-SP1's gate
(`.orbit/gate`); edit the Score and Decisions sections for this repo, keep the rest.

## Score
Pick one, write the command and today's value here, and label it **measured** (a command produced it) or **estimated**:
- architecture: `.orbit/gate fitness` (NavLab fitness violations) + `.orbit/orbit principles check`;
- migration: the surface's ledger metric (`python3 $(.orbit/home)/navlab/arch/ledger.py ...`), or the crossing share
  from `replay.py --map split.json`;
- performance: ms per journey from `perf-climb` (`$H accept`), Release build only.

Score: `<command>` -> **<value>** (<measured|estimated>, <date>). Lower is better.

## Constraints (a move that breaks one is rejected, however much it lowers the score)
1. `.orbit/orbit verify gate`: typecheck, `gate ratchet` (no ruler looser than the base), `gate contracts` (no contract
   lost: storage keys, events, deep links, routes, API calls, native modules), `gate fitness` (no new violation).
2. `.orbit/orbit verify gate-device`: every must-pass simwalk flow in `flows/` passes 3 times on a simulator you
   claimed; every `xfail` flow still fails (a demonstrated bug stays a failing flow until it is fixed).
3. Judge on a Release build for anything timed. Debug numbers are not evidence.

## One move
1. Pick the cheapest violation or gap (the score's command lists them). Prefer moves that make it unrepresentable:
   a type over a gate, a gate over a test.
2. Write the acceptance test first and watch it fail: an `xfail` flow, a fitness rule, a ledger prediction.
3. Get context: `.orbit/orbit packet <unit> "<the violation>"`.
4. Make the smallest diff that removes it. One violation class per commit.
5. Run both check sets. All green and the score dropped: tighten (`fitness.py init` only ever removes `accepted`
   entries here; drop the `xfail` marker of a flow that now passes), commit code and ruler together, append the log entry
   (HARNESS.md "The log entry"). Otherwise discard the move.

## Off-limits (the gate blocks these; a person can do them, by pushing the change)
- Loosening a ruler: adding `accepted` fitness violations, raising a budget, deleting a rule, removing a contract from
  `.navlab/contracts.baseline.json`, deleting or `xfail`-marking a must-pass flow, deleting an `xfail` flow, making a
  required check optional, dropping a check from a set, un-enforcing a principle, removing the Stop hook.
- Changing a check's command in the same move as the code it judges.
- Dodging a rule instead of fixing it: re-exporting through a barrel to escape an import rule, `// @ts-ignore`,
  `eslint-disable`, renaming a testID a flow uses, or splitting a file into meaningless pieces. A reviewer reads every
  accepted diff for these.

## Enforcement
`.orbit/gate stop` is the Claude Code Stop hook (`.claude/settings.json`). When the tree differs from the base it runs
`verify gate`, and if app files changed it requires `gate-device` runs newer than the newest change. Red blocks the
Stop once with the reasons; a second Stop goes through and tells the user instead.

## Decisions taken
- (date) what was decided, why, and what it rules out.
