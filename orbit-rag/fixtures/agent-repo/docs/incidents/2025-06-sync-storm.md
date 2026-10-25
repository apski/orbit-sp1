# Incident: sync storm after 3.1.0 (June 2025)

## Summary

After 3.1.0, a backend outage caused clients to retry uploads aggressively. Retries from roughly 40,000 devices pushed the API past its capacity for 50 minutes after the outage itself ended.

## Cause

The 2.9.0 retry policy (backoff from 1 second, 10 attempts) was still in place, and clients ignored `Retry-After` on 503 responses.

## Remediation

Backoff now starts at 2 seconds, retries are capped, clients honour `Retry-After` for 429 and 503, and a server-side kill switch can pause background sync remotely.

## Follow-ups

Load-test the sync path before each minor release. Add a dashboard for queue depth across the fleet.
