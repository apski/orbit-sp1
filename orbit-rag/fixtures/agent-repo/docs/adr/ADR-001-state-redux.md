# ADR-001: Client state with Redux Toolkit

Status: accepted

## Context

In 2023 the app had three different ad-hoc state solutions and no conventions for side effects.

## Decision

Client state uses Redux Toolkit with one slice per feature and RTK listeners for side effects.

## Consequences

Boilerplate is moderate; devtools are good; selectors must be memoised to avoid re-renders.
