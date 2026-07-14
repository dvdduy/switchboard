# Architecture

## Architectural style

Switchboard begins as a **modular monolith with a separate background worker**. This provides strong transactional boundaries and simple local operation while preserving modules that could later become independently deployed control-plane or data-plane services.

LangGraph is an orchestration adapter. Switchboard owns the public execution model, state machine, persistence, APIs, and guarantees.

## System context

```mermaid
flowchart LR
    U[End User] --> C[Product Chat Client]
    D[Product Developer] --> CP[Switchboard Control APIs]
    T[Tool Developer] --> CP
    O[Platform Operator] --> CP
    C --> DP[Switchboard Conversation API]
    DP --> LLM[Model Provider]
    DP --> EXT[Registered Product Tools]
    CP --> DP
```

## Target container view

This diagram includes later outbox, model, tool, evaluation, and rollout
components. The current Day 7 deployment subset is listed below.

```mermaid
flowchart TB
    Client[Product Client] -->|HTTPS / SSE| API[Switchboard API]
    API --> PG[(PostgreSQL)]
    API --> Redis[(Redis)]
    API --> Outbox[Transactional Outbox]
    Worker[Switchboard Worker] --> PG
    Worker --> Redis
    Worker --> Model[Model Gateway]
    Worker --> Tools[Tool Adapters / External APIs]
    Eval[Eval Runner] --> PG
    Eval --> Model
    Release[Rollout Simulator] --> API
    Release --> PG
```

## Control plane and data plane

### Control plane

- agent and tool registration;
- policy configuration;
- eval dataset and evaluator management;
- release creation and rollout transitions;
- disabling unhealthy tools;
- inspection and audit APIs.

### Data plane

- accept conversation turns;
- persist and dispatch work;
- load context;
- route tools;
- evaluate policies;
- wait for approval;
- invoke tools;
- call models;
- stream committed events;
- recover from retries and failure.

They initially share one codebase and database but have separate modules and contracts.

## Proposed modules

```text
src/switchboard/
├── domain/
│   ├── agents/
│   ├── conversations/
│   ├── execution/
│   ├── tools/
│   ├── policies/
│   ├── evaluation/
│   └── releases/
├── application/
│   ├── commands/
│   ├── queries/
│   ├── ports/
│   └── services/
├── adapters/
│   ├── api/
│   ├── persistence/
│   ├── orchestration/
│   ├── models/
│   ├── tools/
│   ├── streaming/
│   └── telemetry/
├── workers/
└── bootstrap/
```

## Target runtime turn flow

The following end-to-end outbox, worker, routing, policy, tool, and model flow is
the target architecture; Days 3–6 implement the durable event/SSE, context,
tool-registry control-plane, and public conversation-acceptance subsets
described below.

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant DB as PostgreSQL
    participant Worker
    participant Router
    participant Policy
    participant Tool
    participant Model

    Client->>API: POST conversation turn
    API->>DB: message + turn + outbox (one transaction)
    API-->>Client: 202 Accepted + turn ID
    Worker->>DB: claim outbox / turn
    Worker->>DB: append ROUTING event
    Worker->>Router: select eligible tool or fallback
    Router-->>Worker: structured routing decision
    Worker->>Policy: evaluate action
    alt confirmation required
        Worker->>DB: persist approval request and pause
        API-->>Client: SSE approval.required
        Client->>API: approve request
        API->>DB: persist approval and dispatch resume
    end
    Worker->>Tool: invoke with stable idempotency key
    Tool-->>Worker: structured result
    Worker->>Model: generate final response
    Model-->>Worker: streamed tokens/events
    Worker->>DB: persist committed events and completion
    API-->>Client: SSE committed events
```

## Durable dispatch

Target architecture, not yet implemented: the API transaction will commit:

1. user message;
2. `TurnExecution` in `RECEIVED`;
3. initial execution event;
4. outbox record.

The future worker will claim outbox records and advance the state machine. Day 7
durably accepts a message, turn, pending attempt, and command receipt, but does
not yet provide the transactional outbox, durable claiming, or recovery.

## Conversation API

The implemented `/api/v1` boundary exposes create/continue commands,
conversation metadata, exclusive-cursor message history, turn inspection, and
the existing event stream. Pydantic transport models and stable error envelopes
are separate from domain entities and SQLAlchemy records; routes delegate to
application services and issue no SQL.

Every operation requires an explicit `X-Team-ID` UUID. It is a development
ownership context, not authentication. Commands also require an opaque
`Idempotency-Key`. PostgreSQL command receipts store its SHA-256 hash, a
versioned canonical request fingerprint, and immutable result identifiers.
Receipt uniqueness serializes duplicate commands; the receipt and accepted
conversation graph commit in the same unit of work. Identical replay returns
the original result, while conflicting reuse is rejected.

`202 Accepted` means the message, received turn, pending attempt, and receipt
are durable. It does not mean execution started. No route launches background
work; tests invoke the deterministic runner explicitly when they need terminal
events. A transactional outbox remains necessary to bridge acceptance to
automatic durable dispatch.

## Execution state machine

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> ROUTING
    ROUTING --> NEEDS_CLARIFICATION
    ROUTING --> AWAITING_CONFIRMATION
    ROUTING --> READY
    NEEDS_CLARIFICATION --> COMPLETED
    AWAITING_CONFIRMATION --> READY: approved
    AWAITING_CONFIRMATION --> CANCELLED: rejected/expired
    READY --> EXECUTING_TOOL
    EXECUTING_TOOL --> GENERATING_RESPONSE
    EXECUTING_TOOL --> UNKNOWN_OUTCOME
    EXECUTING_TOOL --> FAILED
    UNKNOWN_OUTCOME --> GENERATING_RESPONSE: reconciled success
    UNKNOWN_OUTCOME --> READY: reconciled safe retry
    UNKNOWN_OUTCOME --> FAILED: terminal review
    GENERATING_RESPONSE --> COMPLETED
    RECEIVED --> CANCELLED
    ROUTING --> FAILED
    GENERATING_RESPONSE --> FAILED
```

Not every turn calls a tool. A direct-response path may move from routing to generation.

## Tool-routing pipeline

```mermaid
flowchart LR
    Registry[Active bound tools] --> Auth[Authorization filter]
    Auth --> Health[Availability filter]
    Health --> Retrieve[Embedding candidate retrieval]
    Retrieve --> Select[Structured final selection]
    Select --> Outcome{Outcome}
    Outcome -->|Selected| Policy[Policy evaluation]
    Outcome -->|Ambiguous| Clarify[Clarifying question]
    Outcome -->|No match| Fallback[Fallback response]
    Outcome -->|Unavailable| Explain[Availability fallback]
```

## Policy boundary

The policy engine receives a complete request context and returns one of:

- `ALLOW`;
- `DENY`;
- `REQUIRE_CONFIRMATION`;
- `REQUIRE_ELEVATED_APPROVAL`.

The executor never bypasses the policy result. Approvals are separate durable records with actor, scope, expiration, and argument fingerprint.

## Tool execution contract

Each invocation includes:

- immutable tool version;
- validated arguments;
- stable logical invocation ID and idempotency key;
- caller identity and delegated scopes;
- timeout and retry policy;
- trace context.

Each result maps to a platform error taxonomy. A timeout after dispatch of a mutation may become `UNKNOWN_OUTCOME`; it is not blindly retried.

## Streaming model

SSE is used because the primary direction is server-to-client event delivery.
Implemented events have monotonically increasing turn-local sequence numbers
allocated under a PostgreSQL turn-row lock. A reconnecting client supplies a
non-negative `Last-Event-ID`; the API treats it as an exclusive cursor, replays
committed events in order, and then follows newly committed events.

The execution services emit stable `response.delta` chunks rather than exposing
provider token objects. Day 7 adds safe `tool.started`, `tool.completed`, and
`tool.failed` events without arguments, results, exceptions, prompts, or private
reasoning. A framework-independent replay service polls PostgreSQL
with short independent units of work, never sleeps or yields with a transaction
open, and closes after a terminal event. Redis is not required for correctness;
notification-assisted polling remains a future optimization.

`GET /api/v1/turns/{turn_id}/events` is read-only. Disconnecting or cancelling
one observer does not mutate execution or affect another observer. Production
retention and chunk-size tuning remain undefined.

## Context management

Every immutable `AgentVersion` owns a typed `ContextPolicy`: total model-window
tokens, reserved output, fixed instruction/tool overhead, maximum summary size,
and a mandatory recent-message floor. The application computes conversation
capacity as the model window minus reserved output and fixed overhead. It fails
explicitly when mandatory context cannot fit.

`BuildTurnContext` reconstructs a turn from messages only through that turn's
input-message sequence. A deterministic assembler keeps the newest contiguous
suffix and, when required, represents the omitted prefix with an immutable
`ConversationSummary`. Summaries start at sequence 1 and record conversation,
agent version, coverage, summarizer version, token-counter version, token count,
and creation time. They are derived artifacts, not visible conversation
messages or authorization evidence.

Snapshot reads, compatible-summary lookup, summarization, and summary writes use
separate boundaries. No database transaction remains open while a summarizer is
running. PostgreSQL uniqueness selects one authoritative artifact when
concurrent builders summarize the same provenance and coverage. The application
validates tenant ownership before reading or creating summaries.

The token counter and summarizer are ports. The current local summarizer is a
deterministic extractive simulator, not a production tokenizer or model-backed
semantic summarizer. Day 7 passes the resulting pinned context into the explicit
bounded orchestration workflow through provider-independent contracts.

## Tool registry and conformance

`ToolDefinition` is the stable team-owned identity and `ToolVersion` stores one
immutable validated manifest plus a canonical content hash. Mutable availability
is isolated in `ToolVersionState`, whose revision supports compare-and-set
activation, deprecation, and disable transitions. Activation records the exact
successful `ToolConformanceRun` that tested the version.

Manifest validation accepts bounded JSON Schema Draft 2020-12 object contracts,
rejects remote references, exposes no executable/credential/endpoint
configuration fields, freezes JSON recursively, and returns ordered safe
diagnostics without rejected values. It does not semantically detect secrets in
arbitrary descriptions or schema annotations; callers must submit sanitized
control-plane text.
Effect, scope, timeout, retry, idempotency, reconciliation, adapter key, and
redaction declarations are part of the immutable contract.

`ToolAdapter` and `ToolAdapterResolver` are application ports. Conformance invokes
an installed adapter without an open database transaction, validates synthetic
inputs and normalized outputs, bounds calls by timeout, checks declared errors,
idempotency propagation, reconciliation, and redaction, then persists the
complete run and case results in one short unit of work. Cancellation before the
write persists no partial run.

Binding an active exact tool version clones the base immutable `AgentVersion`
and its existing bindings under the agent-definition lock. The eligible query
returns only same-team exact bindings whose current lifecycle is `ACTIVE` and
whose activation run passed. Day 7 additionally filters a trusted development
scope set and permits only read-only tools, then locks and revalidates the exact
version immediately before dispatch. Production actor authorization, live-health
filtering, semantic routing, and approval remain downstream responsibilities.

The current resolver contains deterministic local `search_work_items` and
`update_due_date` examples. It is not dynamic code upload or production service
discovery; HTTP, MCP, queue, secret, and external SaaS adapters remain future
implementations of the same ports.

## Day 7 bounded orchestration and explicit execution

Application ports define normalized model actions, orchestration requests, and
the durable tool-call callback. Only `switchboard.adapters.orchestration` imports
LangGraph. Its ephemeral typed graph permits either a direct response or one
read-only tool call followed by a final response; it has an explicit recursion
limit and no framework checkpointer. The deterministic model gateway makes both
paths testable without credentials or network access.

`RunTurn` is an explicit application workflow, not an HTTP background task. It
compare-and-sets the turn and attempt to running, builds bounded context, loads
eligible descriptors, and invokes the graph without an open database
transaction. A requested tool first creates a durable `PENDING` invocation with
a stable key. Under a short locked transaction, the application revalidates
ownership, binding, lifecycle, conformance, scopes, and read-only effect, then
commits `RUNNING` with `tool.started`. Adapter work occurs outside the
transaction, followed by a short terminal invocation/event commit.

Final assistant-message insertion, turn/attempt success, and `turn.completed`
are atomic. Failure after committed progress appends one safe terminal
`turn.failed`; tool arguments, outputs, provider exceptions, prompts, and hidden
reasoning never enter public events. Automatic dispatch, crash recovery, real
model providers, semantic selection, and mutating execution are deferred.

## Persistence ownership

- PostgreSQL: source of truth for configuration, conversation, execution, approval, audit, eval, and release state.
- Redis: ephemeral cache, rate-limit counters, leases, and connection coordination.
- Object storage: deferred; may later hold large eval artifacts or traces.

## Evaluation architecture

Offline evaluation is a control-plane job. It pins versions, runs deterministic checks first, optionally runs a calibrated judge, writes case-level results, compares against a baseline, and emits a pass/fail release decision.

Live rollout protection consumes operational signals. It does not rerun the entire offline golden dataset on every production turn.

## Deployment

Current Docker Compose services:

```text
api
worker
postgres
postgres-test (test profile)
redis
```

Planned additions:

```text
eval-runner
rollout-simulator
```

The API and worker use the same application/domain packages but different entry points.

## Scaling path

Only when measurement justifies it:

1. scale workers horizontally with database-backed claiming;
2. separate eval workloads from conversation workers;
3. move durable dispatch to a managed queue while preserving outbox semantics;
4. isolate control-plane APIs;
5. partition high-volume execution-event storage;
6. introduce multi-region ownership rules.

No microservice split is required merely to demonstrate seniority.


## Implementation status after Day 7

Implemented:

- one repository with separate API and worker entry points;
- inward domain/application/adapters/bootstrap dependency direction;
- FastAPI health and readiness endpoints;
- PostgreSQL and Redis runtime resources;
- SQLAlchemy Core persistence, Alembic, repository ports, and unit of work;
- durable versioned-agent, conversation, message, turn, and attempt records;
- atomic conversation start and PostgreSQL integration tests;
- immutable JSON-compatible execution events associated with logical turns and
  optional physical attempts;
- turn-local event sequence allocation, locked append, exclusive-cursor reads,
  lifecycle compare-and-set updates, and relational ownership constraints;
- deterministic simulated execution with durable chunks, atomic success output,
  and durable terminal failure after partial progress;
- framework-independent replay-then-tail polling over short transactions;
- reconnectable SSE with exact event IDs/types, compact JSON payloads, preflight
  validation, terminal closure, and independent observers.
- typed immutable context policies pinned to agent versions;
- durable prefix summaries with relational coverage, ownership, provenance, and
  concurrency authority constraints;
- deterministic bounded context selection that preserves the current input and
  configured recent-message floor;
- turn-pinned message cutoffs, compatible summary reuse, and short-transaction
  summary creation through provider-independent ports.
- durable team-owned tool definitions, lock-allocated immutable versions, and
  separate revisioned lifecycle state;
- bounded manifest validation with safe diagnostics and immutable JSON content;
- case-level deterministic conformance persisted only after adapter work;
- exact successful-run activation and immutable agent-version binding clones;
- deterministic local read-only and idempotent/reconcilable mutating adapters;
- an eligible query filtered by binding, team, active state, and conformance.
- versioned create/continue, conversation, message-history, and turn APIs;
- PostgreSQL-backed command receipts with hashed keys, canonical request
  fingerprints, atomic graph creation, deterministic replay, and conflict
  detection;
- bounded strict DTO validation, stable sanitized errors, development team
  ownership checks, deterministic pagination, and OpenAPI examples;
- team-aware SSE preflight and external-client contract/concurrency coverage.
- provider-independent model/orchestration contracts and a deterministic
  structured model gateway;
- a framework-isolated, bounded LangGraph adapter with direct and single-tool
  paths and no durable framework state;
- durable tool invocations with relational ownership, stable keys, canonical
  immutable arguments, compare-and-set lifecycle transitions, and safe events;
- locked exact-version read-only dispatch with trusted development scopes and no
  transaction held across adapter execution;
- explicit durable turn execution with pinned context, atomic terminal success,
  safe partial failure, and end-to-end SSE/history coverage.

Planned but not yet implemented:

- transactional outbox and worker claiming;
- durable worker recovery;
- real model-provider execution;
- Redis-assisted event notification;
- event retention and production chunk-size tuning;
- production tokenizers and semantic summarizers;
- summary chaining, retention, deletion, and large-history optimization;
- semantic tool routing, production authorization and health filtering,
  policies, approvals, real model providers, evaluation, and rollout control;
- automatic execution dispatch, invocation recovery/retries, and
  unknown-outcome reconciliation;
- public registry-management APIs, production HTTP/MCP/queue adapters, and
  conformance retention or production telemetry policy.
- production authentication/authorization, rate limits, quotas, and opaque
  retention-aware history cursors.
