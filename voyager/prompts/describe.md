You are writing a task for an engineer who will fix a bug in {{REPO_NAME}} without seeing the real fix.
You can read the code at the starting commit in {{WT}} (read-only; do not edit or commit).

## What really happened (for you only, never repeat the fix)

Commit message of the real fix:
{{MESSAGE}}

The real fix (source diff):
{{FIX_DIFF}}

The tests that were added/changed with it (these fail on the starting code):
{{TEST_DIFF}}

## Write

- `title`: one line, the symptom.
- `description`: under 150 words, written like a real bug report from someone OUTSIDE the code: what is observed
  vs what should happen, how to trigger it (inputs, the entry point a caller uses), and how big the impact is.
  NO diagnosis and NO solution: do not explain WHY it happens internally (which value has the wrong type, which
  lookup collides, which branch is missing), and do not say where or how to fix it. The engineer must find the
  cause themselves, as they would with a real report.
- `interface`: if the tests call a function, method, argument or attribute that does not exist at the starting
  commit, give its exact name and signature (a test can't pass otherwise). Otherwise an empty list.
- `acceptance`: 2-5 observable behaviours the real fix establishes (its tests check them), each one line, no
  implementation detail. Nothing the real fix did not do.
