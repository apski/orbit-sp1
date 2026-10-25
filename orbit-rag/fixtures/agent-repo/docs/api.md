# Tidepool API conventions

## Style

The public API is REST over HTTPS with JSON bodies. Resource names are plural nouns (`/spots`, `/alerts`), identifiers are opaque strings, and timestamps are RFC 3339 in UTC. All endpoints live under the `/v3` prefix.

## Authentication

Every request carries the session token in the `X-Tidepool-Token` header. Tokens are short-lived (15 minutes) and refreshed with the refresh endpoint; the refresh token never leaves the keychain or keystore.

## Pagination

List endpoints use cursor pagination. Pass the cursor from the previous page in the `after` query parameter; page size is set with `limit` (default 25, maximum 100). Offset pagination is not supported.

## Rate limits

Clients are limited to 120 requests per minute per token. A 429 response carries `Retry-After` in seconds; the client must honour it and must not retry earlier.

## Uploads

Photo uploads up to 25 MB are accepted; larger files get a `413`.

## Errors

Errors use a single envelope: `{ "error": { "code": "spot_not_found", "message": "…", "request_id": "…" } }`. Codes are stable snake_case strings; messages are for humans and may change.
