# Day 6 — Shared Versioned Conversation API

**Status:** Planned
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

## Design questions to resolve

1. Idempotency scope, recommended team + operation + conversation as applicable.
2. Whether create includes an initial turn; preserve the existing atomic flow.
3. Sequence cursor versus opaque cursor; sequence is acceptable for Phase 1.
4. Whether demo execution needs a development-only command; prefer CLI/worker.
5. Explicit versus default agent-version selection.

## Build checkpoints

### Checkpoint 0 — Contract and compatibility review

- map FR-001/FR-002 and UC-02/UC-08 to endpoints;
- write examples and stable error codes;
- decide idempotency scope/fingerprint;
- confirm no endpoint launches nondurable work;
- define OpenAPI naming and error conventions.

### Checkpoint 1 — Continue-conversation workflow

Build an atomic use case that validates ownership/status, pins agent version,
appends a user message under row lock, creates one turn and pending attempt,
persists idempotency metadata, returns identical replay, rejects conflicting
reuse, and leaves no partial records on failure.

### Checkpoint 2 — DTOs, errors, and dependency wiring

Add versioned Pydantic models, stable error envelope, mappings for validation/
not-found/conflict/ownership/lifecycle, dependencies for clocks/IDs/UoW, safe
response links, and explicit local team context.

### Checkpoint 3 — Create and continue endpoints

Implement atomic create, continue, consistent `202`, idempotent replay, content
limits, ownership/version validation, and exact OpenAPI examples. Client
disconnect after commit must not invalidate accepted state.

### Checkpoint 4 — Read models and pagination

Implement query services/endpoints for conversation metadata, ordered history,
turn/attempt summary, and stream links using short read transactions. Expose only
safe public data.

### Checkpoint 5 — Contract and external-client tests

Prove an external test client can create, replay idempotently, continue, page
history, inspect turns, and connect to the event URL after explicit test
execution.

Cover malformed IDs/bodies, oversized content, idempotency conflicts,
cross-team access, closed conversation, unknown agent, pagination bounds,
stable errors, and OpenAPI.

### Checkpoint 6 — Documentation and verification

Update README examples, architecture/API notes, requirements/use-case evidence,
operations instructions, `PROGRESS.md`, and this plan. Run all quality gates,
migration round-trip when needed, container build, and OpenAPI smoke checks.

## Required tests

### Unit/application

- fingerprint determinism;
- replay/conflict;
- ownership/status;
- atomic continuation;
- pagination validation.

### API contract

- status/error codes;
- schemas/OpenAPI;
- ordering/pagination;
- SSE link compatibility;
- cross-team non-disclosure.

### PostgreSQL/concurrency

- idempotency uniqueness;
- rollback without partial records;
- duplicate commands produce one result;
- distinct concurrent turns get distinct message sequences.

### Architecture

- routes delegate to use cases;
- domain imports no FastAPI/Pydantic;
- API issues no ad hoc SQL.

## Migration impact

Likely a durable command receipt/idempotency table, or equivalent fields and
indexes. An in-memory cache is not a correctness mechanism.

## Security and safety considerations

- Team context is development identity, not authentication.
- Ownership is checked before disclosure.
- Bound message/page/header sizes.
- Sanitize errors/logs.
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

- [ ] External clients create/continue through `/api/v1`.
- [ ] Commands are atomic and durably idempotent.
- [ ] Conflicting key reuse is rejected.
- [ ] History and turn reads are stable/documented.
- [ ] Responses link to reconnectable SSE.
- [ ] No route launches untracked durable work.
- [ ] Cross-team access is safely rejected.
- [ ] API, persistence, concurrency, architecture, and full gates pass.
- [ ] README, OpenAPI, plan, and `PROGRESS.md` agree.

## Suggested commit

`feat(api): add versioned conversation commands and history`

## Earn

You can demonstrate a consumer using a documented conversation API, explain
command idempotency, and show why execution is independent from HTTP lifetime.

## Assumptions to revisit

- A transactional outbox will close the acceptance-to-dispatch gap.
- Production auth will replace local team context.
- History may later need opaque/retention-aware cursors.
