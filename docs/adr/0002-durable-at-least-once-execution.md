# ADR-0002: Durable At-Least-Once Execution with Idempotent Effects

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

Workers, networks, models, and external tools can fail at any boundary. A worker may process the same logical invocation more than once, and an external mutation can succeed even when its response is lost.

Exactly-once execution cannot be honestly guaranteed across arbitrary external systems.

## Decision

- Persist turns and dispatch intent atomically through a transactional outbox.
- Allow at-least-once delivery to workers.
- Assign one stable logical invocation ID and idempotency key across retry attempts.
- Require external-side-effect tools to declare idempotency and reconciliation capabilities.
- Represent uncertain mutations as `UNKNOWN_OUTCOME`.
- Reconcile unknown outcomes before retrying.

## Alternatives considered

### Claim exactly-once execution

Rejected as misleading across independent transactional boundaries.

### Never retry mutations

Rejected because safe transient failures would become unnecessary user-visible failures.

### Retry every timeout

Rejected because a timeout can occur after an external system has committed the operation.

## Consequences

- Domain and tests must distinguish logical invocation from delivery attempt.
- Tool contracts become richer.
- Some uncertain cases require review.
- The system can explain its guarantees precisely in interviews and documentation.
