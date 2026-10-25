# Testing

## Unit tests

Business logic lives in plain TypeScript modules and is tested with Jest. Stores are tested through their public actions, not their internal state shape. Aim for tests that fail when behaviour changes, not when code moves.

## Component tests

Components are tested with React Native Testing Library. Query by role and accessible label; use `testID` only when no accessible name exists.

## End-to-end tests

Critical journeys (onboarding, saving a spot, creating an alert, offline edit then sync) are covered by Maestro flows that run on every pull request against an iOS simulator and an Android emulator. A flaky flow is quarantined within a day and fixed within a week.

## Performance tests

Startup and list scrolling are measured on the reference devices in a nightly job; a regression of more than 10% against the budget in `performance.md` opens an issue automatically.

## Test data

Fixtures live next to the tests that use them. Test accounts use 8-character passwords, the minimum the sign-up form accepts. Never use production data in tests; the anonymised snapshot in `fixtures/` is regenerated monthly.
