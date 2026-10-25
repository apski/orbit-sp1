---
name: ds-audit
description: "Audit a React Native app's design system and components: inventory every component with uses and prop variants, find competing implementations of the same component (a DS package vs local copies, a migration in progress), near-duplicate families (the seven-buttons problem), token adherence and palette drift, and an RN to native (SwiftUI/UIKit) parity matrix that measures the dual-design-system cost of migrating. Use when the user says 'design system audit', 'component inventory', 'duplicate components', 'token adherence', 'hard-coded colors', 'DS parity', 'what would we need natively', or before a migration or redesign."
---

# Design system audit

Two questions: **is there one design system, and is it used?** And for migration: **what would have to exist
natively, and what does keeping two in sync cost?**

Preflight: `python3 $NAVLAB_HOME/arch/profile.py check ds-audit`. The profile names the design-system roots
(`facts.designSystem`: folders and/or packages) and the native DS folder. Guessing by folder name works, but
it's wrong on apps whose "components" folder is app code.

```bash
node $NAVLAB_HOME/arch/facts.mjs .
python3 $NAVLAB_HOME/arch/ds.py .navlab/facts.json [--ds src/ui,@company/ui] [--scope .navlab/<f>-dep.json] [--native-ds ios/DesignSystem] --json .navlab/ds.json
```

## Reading it
- **same name, several sources:** e.g. `Text: @company/ui ×476 | src/components/Text ×241 | react-native ×94`. Two
  design systems coexist, or a migration stalled halfway. Decide the target, then retire the other with a fitness rule.
- **near-duplicate families:** components of one kind (Button, Card, Header...) whose call sites pass overlapping
  props. They're candidates to merge into one component with variants. Confirm by reading both; similar props are a
  hint, not proof.
- **tokens:** adherence = token refs ÷ (token refs + literal colors + magic numbers). Palette drift = distinct literal
  colors. The worst files are where to start.
- **parity (`--native-ds`):** DS components with no native counterpart. With `--scope`, that's the port list for one
  migration surface. Each missing component is port work plus permanent dual maintenance.

## Where it feeds
- `rn-native-migration`: the dual-design-system tax. Missing native components on a surface go into the ROI cost, and
  the shared-token pipeline (one token source generating both RN and Swift) becomes a prerequisite.
- `design-afresh`: duplicates inside a feature go to `dropped`; the target design uses the DS.
- `app-architecture-steward`: a DS consolidation move with ledger metrics (adherence %, families merged, competing sources).
- Fitness functions: lint rules banning imports from the retired DS, and literal colors outside theme files.

## Limits
Static. Re-exports through layout files are resolved when the declaring file is unique. Styled-components and
Tamagui/NativeWind classes aren't parsed yet (they show as low literal counts, not as adherence).

## When this skill falls short
Log it before working around it: `python3 $NAVLAB_HOME/arch/gaps.py add --skill ds-audit --kind <missing-pattern|false-positive|false-negative|missing-tool|missing-skill|missing-data|wrong-answer|slow|ux> --impact <blocked|wrong-answer|degraded|slow> --repo <short name> --what "..." --evidence <file:line> --workaround "..."`. List what you logged in the report. Rules: AGENTS.md "Learning loop".
