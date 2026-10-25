# Tidepool mobile architecture

Tidepool is a tide-and-surf forecasting app for iOS and Android built with React Native 0.81 and a thin native shell.

## Navigation

Native owns navigation while React renders screens. Each tab hosts a UIKit coordinator (Android: a Jetpack Navigation graph) and React Native screens are mounted per route through a shared `ScreenHost`. Deep links resolve in native code before any JavaScript runs, so cold-start deep links do not wait for the bundle.

Screens must not import a JavaScript navigation library. Modal presentation, sheets and the tab bar are native.

## State

Client state lives in Redux Toolkit slices, one per feature. Server state is cached with TanStack Query; screens never hold fetched data in component state for longer than a render.

## Platform support

The minimum supported iOS version is iOS 16. The minimum supported Android API level is 29. Tablets are supported in portrait and landscape; foldables are best-effort.

## Data

Forecasts are fetched from the Tidepool REST API under `/v2` (see `api.md`). Saved spots, alerts and session logs are stored locally and synced in the background (see `offline-sync.md`). Crash reports are sent to Bugsnag from both the native shell and the JavaScript runtime.

## Notifications

Push notifications are sent through OneSignal; the native shell registers the device token at launch.

## Maps

Spot maps render with MapLibre and self-hosted vector tiles.
