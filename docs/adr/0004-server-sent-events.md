# ADR-0004: Use Server-Sent Events for Initial Streaming

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

The main live communication pattern is server-to-client delivery of turn status, approval requests, and generated response chunks. Client commands such as submitting a turn or approval can use ordinary HTTP requests.

## Decision

Use Server-Sent Events for the initial public streaming API. Assign monotonic turn-local event IDs and support reconnect using `Last-Event-ID` with replay from durable committed events.

## Alternatives considered

### WebSockets

Useful for high-frequency bidirectional communication, but adds connection and protocol complexity not required for the first use cases.

### Polling

Simple but inefficient and provides a weaker streaming experience.

### Raw token stream without durable event IDs

Rejected because reconnect and incident reconstruction would be unreliable.

## Consequences

- Simple browser/client support and proxy-friendly HTTP semantics.
- Client-to-server operations remain separate HTTP endpoints.
- Durable event chunking and retention need explicit design.
- WebSockets may be added later without changing the execution-event model.
