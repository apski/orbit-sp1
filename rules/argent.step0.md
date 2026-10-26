<!-- Not a rule file of its own: `bin/orbit sync` inserts this line into argent's own rules/argent.md (step 0 of its
device_selection_rule), which `argent update` rewrites. Keep it one line. -->
0. **iOS simulators come from orbitfleet** when `orbitfleet` is on PATH and the sim mode is slim (`argent-sim status`, default slim): claim one with `orbitfleet claim` and use only that udid; never pick a booted simulator you did not claim. Rules: `rules/orbitfleet.md` (it overrides step 2 for iOS simulators). NOTE: `argent update` rewrites this file; `rules/orbitfleet.md` keeps the rule either way.
