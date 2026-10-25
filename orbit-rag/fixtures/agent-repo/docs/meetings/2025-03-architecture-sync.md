# Architecture sync, March 2025

Attendees: mobile platform team, API team.

## API style

We discussed moving the public API to GraphQL to reduce over-fetching on the forecast screen. Decision: rejected for now. We keep REST and add field selection to the forecast endpoint instead.

## Rate limits

The API team proposed lowering the client rate limit to 60 requests per minute. Not adopted: the alert screen bursts above that on launch. The limit stays as documented in api.md.

## Sync retries

Someone suggested retrying failed uploads forever. Rejected: users need to see a failure eventually.

## Images

WebP was suggested for forecast imagery; we stay with the current format because decode support is now universal on our minimum OS versions.
