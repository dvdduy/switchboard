# Day 3 — Durable Execution Events and Reconnectable SSE

**Status:** Complete

## Goal

Deliver the original Day 3 streaming demonstration over a durable,
reconnectable execution-event log rather than coupling generation to an HTTP
request.

## Design decisions

1. SSE is a delivery protocol, not the execution engine.
2. Immutable execution events belong to the logical turn and may identify the
   physical attempt that emitted them.
3. Event order uses a positive turn-local sequence allocated under a
   PostgreSQL row lock.
4. Provider tokens are not the public contract; Switchboard emits stable
   response chunks.
5. The producer and SSE observer remain separate application paths.
6. Only committed events may be delivered to clients.
7. `Last-Event-ID` is an exclusive reconnect cursor.
8. Replay occurs before tailing, and polling uses short transactions.
9. PostgreSQL is the correctness source of truth; Redis is only a later
   notification optimization.
10. Client disconnect does not imply cancellation.

## Build checkpoints

### Checkpoint 0 — Reconcile documentation and branch state

Verify the working tree, tracked source-of-truth files, current branch, remote,
and latest Day 2 commit.

### Checkpoint 1 — Execution-event domain contract

Build `ExecutionEventId`, JSON-compatible immutable payloads,
`ExecutionEventKind`, `ExecutionEvent`, `Turn.next_event_sequence`, and
`Turn.allocate_event_sequence()`.

### Checkpoint 2 — Persistence and migration

Add `turns.next_event_sequence`, `execution_events`, translators, repository
ports/adapters, locked append, exclusive cursor query, and focused lifecycle
updates. Prove migration, ordering, concurrency, rollback, and attempt
ownership with PostgreSQL tests.

### Checkpoint 3 — Simulated durable execution

Build `SimulateAssistantResponse` that commits `turn.started`, deterministic
`response.delta` chunks, the final assistant message, and exactly one terminal
event while transitioning the turn and first attempt safely.

### Checkpoint 4 — Replay-then-tail service

Build a framework-independent async iterator that replays events after an
exclusive cursor, polls with short units of work, emits new committed events,
and ends after a terminal event.

### Checkpoint 5 — SSE API

Expose `GET /api/v1/turns/{turn_id}/events`, support `Last-Event-ID`, validate
cursors before streaming, return `404` for missing turns, serialize exact SSE
frames, and prove reconnect and independent observers.

### Checkpoint 6 — Documentation and verification

Update progress, domain and architecture implementation notes, README examples,
and any affected ADR. Run the full quality gate, PostgreSQL integration tests,
container build, and CI.

## Completion evidence

- immutable JSON-compatible execution events use positive turn-local sequences;
- PostgreSQL appends allocate sequence numbers under a turn-row lock and enforce
  attempt ownership plus at most one start and one terminal event per turn; the
  simulator emits exactly one of each;
- the deterministic simulator persists public response chunks, atomic assistant
  message/success completion, and durable failure after partial progress;
- the framework-independent observer preflights turns, replays after an exclusive
  cursor, polls with short units of work, and isolates observer cancellation;
- `GET /api/v1/turns/{turn_id}/events` validates `Last-Event-ID`, emits compact
  SSE frames, replays committed history, and tails running turns;
- Ruff formatting/linting, strict mypy, the full test suite, PostgreSQL integration
  suite, Alembic downgrade/upgrade, and the container build pass.

Day 3 closes with the intentionally deferred debt listed below. CI configuration
contains the same format, lint, type, test, and container-build gates; this local
closure does not claim that an unpushed GitHub Actions run occurred.

## Out of scope

- create/continue-conversation HTTP commands;
- transactional outbox and durable worker claiming;
- real model providers and LangGraph;
- Redis Pub/Sub as a correctness dependency;
- tool calls, approval, and cancellation APIs;
- event retention and production chunk-size tuning.

The implementation also intentionally retains polling before a future Redis
notification optimization and uses a deterministic simulator rather than a real
model provider.

## Suggested commit

`feat(streaming): add durable reconnectable turn event stream`

## Earn

Switchboard streams a delivery view over a durable execution-event log.
Committed events use monotonic turn-local IDs as reconnect cursors, execution
is independent from HTTP observers, and transport disconnect never defines
execution cancellation.
