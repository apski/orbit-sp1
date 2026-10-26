# Pre-registration: one-shot vs factory on a private app's history

Written 2026-10-25, before any arm ran on the task set (the only runs before this: a 2-task pilot, A and B, on
tasks `dadcc1565a` and `2009e8ddc2`, to find bugs in the harness and measure cost; pilot jobs are kept apart).

**Question.** Is a verification layer (Voyager-1's factory: held-out scenarios, protected validator, diff-only
reviewer, retries) worth its cost, or do agents one-shot the work well enough?

**Claim under test.** The factory's value is "known solved", not "more solved": of the changes it calls done, more
are actually correct (precision of done), and it says which ones it couldn't do (escalations).

**Tasks.** Fix commits to a private Python service (a domain engine) from the last 18 months whose own tests fail on the parent and pass on
the fix (fail-to-pass, stable 2x, run without credentials against a throwaway DB). 255 candidates -> 100 tasks.
A seeded sample (seed 7) of 40; each task text is written by a separate session from the commit, symptom-only (no
diagnosis, no fix), then checked for leaks by another session; leaky ones are rewritten once, else dropped.
The repo is private: the model can't have seen these fixes.

**Arms** (same model: Claude Code `--model opus`, same tools, same deny list, no MCP, same 30 min / 80 turn cap):
- **A one-shot**: the task text, "commit your change". The agent may test itself. One attempt; whatever it
  commits is submitted.
- **B factory**: Voyager-1 `mode=factory`: scenario writer -> builder (test-first brief) -> protected validator
  (f2p, flake, regression, weakening, scope, held-out scenarios, mutation floor 0.5) -> reviewer; up to 2 retries
  on feedback; escalates otherwise. Submitted = reached done without escalation.
- **C best-of-3**: three independent one-shots, then a selector session (same model, may run the repo's tests)
  picks one. The equal-spend control: does B win by checking, or just by spending more?

**Judge** (hidden from every arm): the real fix's tests: all fail-to-pass and pass-to-pass tests of the fix commit,
run on the candidate with the fix's test files laid over it. Judge strength is reported per task (mutants of the
real fix killed by its tests). A blind human merge/no-merge review of a sample is optional and reported separately.

**Metrics.** Primary: precision of done = judge-pass / submitted. Secondary: solve rate (submitted and judge-pass,
over all tasks), false accepts (submitted and judge-fail), escalations, $ per correct accepted change, minutes.
Paired exact McNemar tests on false accepts and on solves.

**Decision rule (fixed now).** Build out the factory (Voyager-1 as the default way jobs run) only if B's precision
beats both A and C by >= 15 points, with the paired false-accept test p < 0.1, at <= 3x A's cost per correct
accepted change. Otherwise: ship one-shot + reviewer, keep the validator as an optional check, and stop.

**Known limits.** n = 40 detects only large effects. Python/pytest bugs only (no UI, no TS). The judge is the
human fix's tests, which can be weak (that is measured, not assumed). Task texts are model-written.
