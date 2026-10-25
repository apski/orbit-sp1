# ADR-004: Client state with Zustand

Status: accepted

## Context

After TanStack Query took over server state, the Redux store held little more than UI flags, and slice boilerplate slowed every feature.

## Decision

New screens keep client state in Zustand stores, one store per feature, with selectors subscribed per component. Existing Redux slices are migrated when their feature is next touched.

## Consequences

Less boilerplate and narrower re-renders. We lose Redux devtools; the Zustand devtools middleware covers most needs.
