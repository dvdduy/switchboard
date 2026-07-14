# Day 6 — Shared Versioned Conversation API

**Status:** Complete
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Days 3–5 complete

## Goal

Expose a stable `/api/v1` conversation contract that allows an external client
to create a conversation, continue it with ordered user turns, inspect durable
history and turn state, and discover the reconnectable event stream without
depending on internal repositories or domain types.

## Learn

- A shared internal API needs public-contract discipline.
- Command acceptance and durable execution are separate concerns.
- Idempotency belongs at the command boundary, not only in the worker.
- Transport DTOs, domain entities, and persistence records are different.
- Stable error codes and pagination are platform capabilities.
- `202 Accepted` is honest only when accepted state is durable.

## Why this matters

This implements FR-001, completes the public surface around Day 3 streaming, and
creates the API consumed by the Phase 1 demo.

## Current context

Switchboard exposes health/readiness and read-only turn-event SSE. It has an
atomic start-conversation use case, ordered messages, turns, attempts, and
events, but no create/continue/history HTTP contract, production authentication,
or automatic durable dispatch.

## Accepted direction

1. Add:
   - `POST /api/v1/conversations`;
   - `POST /api/v1/conversations/{conversation_id}/turns`;
   - `GET /api/v1/conversations/{conversation_id}`;
   - `GET /api/v1/conversations/{conversation_id}/messages`;
   - `GET /api/v1/turns/{turn_id}`;
   - retain turn-event SSE.
2. Creation/continuation atomically persist user message, turn, first pending
   attempt, and command-idempotency metadata.
3. Commands require `Idempotency-Key`. Identical replay returns the original
   result; the same key with different content returns conflict.
4. Return `202 Accepted` with identifiers, status, and event-stream URL. Do not
   imply execution has started.
5. Routes never use `asyncio.create_task()` for durable work.
6. Until outbox/worker claiming exists, execution starts through an explicit
   runner outside the public command transaction. Document this limitation.
7. Use explicit local team context; never present it as production auth.
8. Pydantic DTOs never leak SQLAlchemy rows/internal exceptions.
9. History uses deterministic exclusive message-sequence cursors.
10. Ownership checks occur before revealing resource existence.
11. The existing turn-event SSE endpoint gains the same team ownership boundary
    as the new read endpoints.
12. API acceptance does not launch execution. Tests may invoke the existing
    runner explicitly after acceptance to prove stream-link compatibility.

## Requirement and use-case mapping

| Source | Day 6 public evidence |
|---|---|
| FR-001 | Create and continue commands plus conversation, history, and turn reads are available only through documented `/api/v1` contracts. |
| FR-002 | Every accepted turn returns the existing reconnectable SSE URL; team-aware preflight preserves committed replay and exclusive `Last-Event-ID` behavior. |
| UC-02 | The client durably accepts a read-only user request as a received turn; routing, tool execution, and generation remain Day 7+ work. |
| UC-08 | The client follows the returned event URL and reconnects using the existing exclusive event cursor without changing turn state. |

## Resolved contract decisions

### Identity and headers

- Every `/api/v1` conversation, turn, message, and event operation requires
  `X-Team-ID` containing a UUID. It is explicit development identity, not
  authentication.
- Command endpoints additionally require an `Idempotency-Key` containing 1–128
  visible ASCII characters. The value is opaque and compared exactly.
- Raw idempotency keys are not persisted. A SHA-256 key hash participates in
  the durable uniqueness constraint.

### Commands

`POST /api/v1/conversations` requires an explicit immutable agent version and
an initial user message. It preserves the existing atomic start flow by creating
the conversation, sequence-1 user message, received turn, and first pending
attempt together.

```json
{
  "agent_version_id": "11111111-1111-4111-8111-111111111111",
  "initial_user_message": "Which Project Alpha tasks are overdue?"
}
```

`POST /api/v1/conversations/{conversation_id}/turns` accepts one user message.
The new turn pins the conversation's existing `default_agent_version_id`; Day 6
does not add implicit upgrades or per-turn caller overrides.

```json
{
  "user_message": "Only include tasks assigned to me."
}
```

Both commands return `202 Accepted` with the same public response shape. The
status describes the durable turn and does not imply worker execution.

```json
{
  "conversation_id": "22222222-2222-4222-8222-222222222222",
  "message_id": "33333333-3333-4333-8333-333333333333",
  "turn_id": "44444444-4444-4444-8444-444444444444",
  "status": "received",
  "conversation_url": "/api/v1/conversations/22222222-2222-4222-8222-222222222222",
  "events_url": "/api/v1/turns/44444444-4444-4444-8444-444444444444/events"
}
```

Physical attempt IDs remain internal. An identical idempotent replay returns
the original `202` representation even if mutable resource state later changes.

### Durable idempotency

- Create scope: team + `create_conversation` + fixed create scope + key hash.
- Continue scope: team + `continue_conversation` + conversation ID + key hash.
- The request fingerprint is SHA-256 over versioned canonical JSON containing
  the operation, canonical team/scope UUIDs, canonical agent-version UUID when
  applicable, and exact validated message content.
- Only hashes and result identifiers are stored; raw user content and raw keys
  are not copied into the command receipt.
- Same scope/key/fingerprint returns the original result. Same scope/key with a
  different fingerprint returns `409 idempotency_conflict`.
- Receipt creation, message append, turn, and attempt commit in one transaction.
  Failure or cancellation before commit leaves no receipt or partial graph.
- Receipt acquisition precedes conversation-row locking consistently. A
  uniqueness-backed insert-or-read serializes concurrent duplicate commands.

### Reads and pagination

- `GET /api/v1/conversations/{conversation_id}` returns safe conversation
  metadata, including status, pinned default agent version, and timestamps.
- `GET /api/v1/conversations/{conversation_id}/messages` accepts an exclusive
  `after_sequence` cursor defaulting to `0` and a `limit` defaulting to `50` in
  the inclusive range 1–100.
- Message results are strictly ascending and include `items`,
  `next_after_sequence`, and `has_more`. The next cursor is the last returned
  sequence, or the input cursor when no item is returned.
- `GET /api/v1/turns/{turn_id}` returns safe turn state, attempt-number/status
  summaries, and its event URL. It does not expose internal exception text.
- Reads use short independent units of work and do not mutate lifecycle state.
- Sequence cursors remain public for Phase 1. Opaque, retention-aware cursors
  are deferred.

Example message page:

```json
{
  "items": [
    {
      "message_id": "33333333-3333-4333-8333-333333333333",
      "sequence": 1,
      "role": "user",
      "content": "Which Project Alpha tasks are overdue?",
      "created_at": "2026-07-14T18:00:00Z"
    }
  ],
  "next_after_sequence": 1,
  "has_more": false
}
```

### Validation and stable errors

Message content must be non-blank and at most 32,000 Unicode code points.
Path/query/body validation returns `422 invalid_request`; missing or malformed
required headers return `400 invalid_header`. Ownership failures and unknown
resources deliberately share `404 resource_not_found`. Lifecycle rejection and
conflicting idempotency reuse return `409 conversation_closed` and
`409 idempotency_conflict`, respectively.

All non-SSE errors use one envelope and never echo rejected content, raw keys,
SQL, or internal exceptions:

```json
{
  "error": {
    "code": "idempotency_conflict",
    "message": "The idempotency key was already used for a different request."
  }
}
```

OpenAPI component names use the `V1` prefix for transport DTOs. Validation
details, when present, contain only stable field locations and diagnostic codes,
never rejected values. An unauthorized team must receive the same status and
body as an unknown resource, including for the existing SSE endpoint.

### Execution boundary

No Day 6 endpoint invokes the simulator, calls `asyncio.create_task()`, or adds a
development execution command. External-client tests may call the explicit
runner fixture after command acceptance, then connect through the returned SSE
URL. A transactional outbox and worker claiming remain required before accepted
commands can be dispatched automatically.

## Build checkpoints

### Checkpoint 0 — Contract and compatibility review

- [x] map FR-001/FR-002 and UC-02/UC-08 to endpoints;
- [x] write examples and stable error codes;
- [x] decide idempotency scope/fingerprint;
- [x] confirm no endpoint launches nondurable work;
- [x] define OpenAPI naming and error conventions.

### Checkpoint 1 — Durable command receipts

Add the fingerprint/key-hash service, immutable receipt contract, repository
port, PostgreSQL schema, migration, insert-or-read concurrency behavior, and
focused unit/integration tests. Do not add HTTP routes yet.

### Checkpoint 2 — Atomic create and continue workflows

Make the existing start workflow durably idempotent and build continuation.
Validate ownership/status, pin the correct version, append under row lock, create
one received turn and pending attempt, return identical replay, reject conflict,
and leave no partial records on failure.

### Checkpoint 3 — Read services and pagination

Build framework-independent conversation, ordered history, and turn/attempt
queries using short read units of work. Add exclusive cursor/limit repository
queries without loading unbounded history.

### Checkpoint 4 — DTOs, errors, and dependency wiring

Add versioned Pydantic models, stable error handlers, dependencies for clocks,
IDs, UoW, safe response links, explicit local team context, and ownership-safe
SSE preflight. Do not query persistence directly from routes.

### Checkpoint 5 — Conversation command and read endpoints

Wire create, continue, conversation, message-history, and turn routes with exact
OpenAPI examples. Preserve consistent `202`, validation limits, ownership
non-disclosure, and event links. Client disconnect after commit must not
invalidate accepted state, and no route starts execution.

### Checkpoint 6 — Contract, concurrency, and external-client tests

Prove an external test client can create, replay idempotently, continue, page
history, inspect turns, and connect to the event URL after explicit test
execution.

Cover malformed IDs/bodies, oversized content, idempotency conflicts,
cross-team access, closed conversation, unknown agent, pagination bounds,
stable errors, and OpenAPI.

### Checkpoint 7 — Documentation and verification

Update README examples, architecture/API notes, requirements/use-case evidence,
operations instructions, `PROGRESS.md`, and this plan. Run all quality gates,
migration round-trip when needed, container build, and OpenAPI smoke checks.

## Required tests

### Unit/application

- fingerprint determinism;
- key validation and hashing without plaintext persistence;
- replay/conflict;
- ownership/status;
- create and continuation rollback without partial receipts;
- atomic continuation;
- pagination validation.

### API contract

- status/error codes;
- schemas/OpenAPI;
- ordering/pagination;
- SSE link compatibility;
- missing/malformed team and idempotency headers;
- bounded message content without rejected-value echo;
- cross-team non-disclosure.

### PostgreSQL/concurrency

- idempotency uniqueness;
- rollback without partial records;
- duplicate commands produce one result;
- conflicting duplicate commands produce no second graph;
- distinct concurrent turns get distinct message sequences.

### Architecture

- routes delegate to use cases;
- domain imports no FastAPI/Pydantic;
- API issues no ad hoc SQL.

## Migration impact

Add a durable command-receipt table with team, operation, scope, key hash,
request fingerprint, immutable result identifiers, and creation time. Enforce
one receipt per team/operation/scope/key hash and relational ownership of result
identifiers where practical. The uniqueness-backed insert-or-read is the
concurrency authority; an in-memory cache is not a correctness mechanism.

## Security and safety considerations

- Team context is development identity, not authentication.
- Ownership is checked before disclosure.
- Bound message/page/header sizes.
- Sanitize errors/logs.
- Persist hashes rather than plaintext idempotency keys or copied user content.
- Apply team non-disclosure to the pre-existing SSE endpoint.
- Do not expose hidden reasoning.
- Treat user content as untrusted.
- Avoid plaintext secrets in fingerprints.

## Out of scope

- production SSO/RBAC;
- rate limits/quotas;
- automatic outbox dispatch/worker recovery;
- cancellation;
- tool-management APIs;
- semantic routing;
- real provider execution;
- WebSockets;
- version negotiation beyond `/api/v1`.

## Definition of done

- [x] External clients create/continue through `/api/v1`.
- [x] Commands are atomic and durably idempotent.
- [x] Conflicting key reuse is rejected.
- [x] History and turn reads are stable/documented.
- [x] Responses link to reconnectable SSE.
- [x] No route launches untracked durable work.
- [x] Cross-team access is safely rejected.
- [x] API, persistence, concurrency, architecture, and full gates pass.
- [x] README, OpenAPI, plan, and `PROGRESS.md` agree.

## Suggested commit

`feat(api): add versioned conversation commands and history`

## Closure verification

- `uv run ruff format .` — 132 files unchanged;
- `uv run ruff check .` — passed;
- `uv run mypy` — passed for 75 source files;
- `uv run pytest` — 284 passed;
- `uv run pytest tests/integration -q` — 68 passed;
- `uv run alembic downgrade -1` then `uv run alembic upgrade head` — command
  receipt migration round-trip passed and head is `f2a6b7c8d9e0`;
- focused OpenAPI smoke test — passed;
- `docker build --tag switchboard:local .` — passed through WSL Docker.

## Earn

You can demonstrate a consumer using a documented conversation API, explain
command idempotency, and show why execution is independent from HTTP lifetime.

## Assumptions to revisit

- A transactional outbox will close the acceptance-to-dispatch gap.
- Production auth will replace local team context.
- History may later need opaque/retention-aware cursors.
