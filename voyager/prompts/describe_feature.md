You are writing a feature request for an engineer who will build it in {{REPO_NAME}} without seeing how it was
really built. You can read the code at the starting commit in {{WT}} (read-only; do not edit or commit).

## What really happened (for you only, never repeat the implementation)

Commit message of the real feature:
{{MESSAGE}}

The real implementation (source diff):
{{FIX_DIFF}}

The tests that came with it (they fail, or cannot even import, on the starting code):
{{TEST_DIFF}}

## Write

- `title`: one line, the capability being added.
- `description`: under 220 words, written like a product/engineering request from someone who knows WHAT is needed
  and WHY, not HOW: the behaviour wanted, who or what uses it, inputs and outputs, constraints, and the existing
  entry point or area it plugs into. No implementation: no internal design, data structures, algorithms or
  step-by-step plan.
- `interface`: everything the tests call that does not exist at the starting commit, exactly as the tests use it:
  module paths, class and function names, signatures, argument names, return shapes, error types. Without this a
  correct build cannot pass the tests; with more than this you leak the implementation.
- `acceptance`: 3-6 observable behaviours the real feature establishes (its tests check them), one line each.
