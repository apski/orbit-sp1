# Changelog

## 3.2.0
- Sync: abandoned mutations now appear in the sync status sheet with a retry button.
- Forecast screen: field selection on the forecast endpoint cuts payloads by 40%.
- Images: AVIF for forecast imagery, JPEG fallback.

## 3.1.0
- State: first features migrated from Redux Toolkit to Zustand (ADR-004).
- API: rate limit raised from 60 to 120 requests per minute per token.
- Minimum iOS raised from 15 to 16.

## 3.0.0
- Navigation moved to native coordinators; react-navigation removed.
- Design system: `color.brand.*` ramp introduced; `color.primary` deprecated.

## 2.9.0
- Sync retries capped at 10 attempts with backoff starting at 1 second.
- Analytics: user ids hashed with SHA-1 (later replaced).

## 2.8.0
- Client state on Redux Toolkit (ADR-001).
- Images served as WebP.
- Minimum iOS 15, Android API 26.
