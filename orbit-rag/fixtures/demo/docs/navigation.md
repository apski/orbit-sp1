# Mobile app architecture

## Navigation

Architecture requires that Native owns navigation while React renders screens. The
navigation stack, deep links and modal presentation live in UIKit coordinators.

Screen transitions must complete within 16 ms per frame on an iPhone 12.

## Data

The app stores user data in SQLite with WAL enabled. Sync happens in a background
queue and never blocks the main thread.

## Rendering

React Native screens are mounted per route; shared state goes through a single store.
