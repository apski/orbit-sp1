# ADR-006: Lower the client rate limit

Status: accepted

## Context

After the June 2025 sync storm, the API team asked for more headroom during recovery from outages.

## Decision

From release 3.3 the client limit is 90 requests per minute per token. The alert screen's launch burst is spread over two seconds so it stays under the new limit.

## Consequences

Heavy users of the alert screen may see slightly slower first loads.
