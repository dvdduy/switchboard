# ADR 0007 — PostgreSQL Command Receipts for API Idempotency

**Status:** Accepted

## Context

Conversation create and continue requests may be retried after a timeout or
lost response. Retrying must not create duplicate messages, turns, or attempts,
and concurrent requests using the same key must converge on one durable result.
An in-memory cache cannot provide this guarantee across processes or restarts.

The API must also detect accidental reuse of one key for different content
without persisting plaintext keys or duplicating user messages in idempotency
metadata.

## Decision

Switchboard persists an immutable `CommandReceipt` in PostgreSQL for each
accepted conversation command.

- The uniqueness scope is team, operation, command scope, and SHA-256 key hash.
- A versioned canonical request fingerprint distinguishes identical replay from
  conflicting reuse.
- Receipts store immutable result identifiers, not a serialized HTTP response.
- Raw idempotency keys and copied message content are not stored in receipts.
- Receipt acquisition occurs before mutable conversation locking.
- Receipt creation and the accepted conversation graph commit in one unit of
  work.
- A uniqueness-backed insert-or-read is the concurrency authority.

An identical retry returns the original result identifiers. Reusing the same
key in the same scope for a different request returns a stable conflict.

## Alternatives considered

### In-memory deduplication

Rejected because it is process-local, disappears on restart, and cannot
serialize concurrent API replicas.

### Store the raw key and full request

Rejected because it unnecessarily retains client secrets and duplicates user
content. Hashes are sufficient for equality and conflict detection.

### Treat every retry as a new command

Rejected because ambiguous network outcomes would create duplicate durable
conversation state.

### Add idempotency only after worker dispatch exists

Rejected because durable API acceptance already needs retry safety independently
of execution delivery.

## Consequences

- PostgreSQL remains the source of truth for command replay.
- Public command latency includes one receipt claim and its transaction.
- Receipt retention must eventually be defined alongside API retry windows.
- The guarantee covers Switchboard's database transaction only; it does not
  imply exactly-once execution in future external systems.
- A future transactional outbox can share the acceptance transaction without
  replacing command receipts.
