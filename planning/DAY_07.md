# Day 7 — Framework-Isolated LangGraph Agent Loop

**Status:** Complete
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Days 4–6 complete

## Goal

Introduce LangGraph as a replaceable orchestration adapter and execute one
durable turn through either a direct-response path or a single read-only
registered tool, using bounded context, pinned manifests, normalized tool
results, and the existing durable event/message lifecycle.

## Learn

- Orchestration frameworks coordinate steps; they do not own platform truth.
- Durable state/public events must remain stable if LangGraph is replaced.
- A ReAct-style loop needs bounded state and termination.
- Model-requested tools/arguments are untrusted structured input.
- Tool invocation needs stable logical identity before retry/recovery is complete.
- Deterministic fake model/tool adapters keep local CI reliable.

## Why this matters

This is the first end-to-end agent behavior connecting context, registry,
durable execution, and streaming while preserving Switchboard ownership.

## Current context

Expected prerequisites include bounded context, active-bound tool queries,
versioned API, durable SSE, and deterministic reference tools. There is no
policy approval, semantic router, durable outbox/worker recovery, or real model
provider yet.

## Accepted direction

1. Define an application-level `AgentOrchestrator` port.
2. Put LangGraph entirely behind an adapter.
3. Switchboard owns lifecycle, context, eligible tools, invocation identity,
   persistence, public events, final message, and failure classification.
4. LangGraph owns only in-memory transitions for one explicit run.
5. Support direct response or one read-only tool call followed by final response.
6. A fake model emits structured `Respond` or `CallTool` actions.
7. Validate selected tool/arguments against the pinned active manifest.
8. Reject mutating/external/privileged tools before dispatch until Day 8.
9. Persist `ToolInvocation` and stable idempotency key before adapter call.
10. Public tool events contain redacted stable data, never hidden reasoning.
11. Bound maximum steps and one tool call.
12. Execution starts explicitly from worker/demo/tests, not HTTP background work.
13. Use only the base LangGraph package; do not add LangChain or a provider SDK.
14. Compile a custom typed `StateGraph` without a checkpointer and invoke it
    asynchronously with an explicit recursion limit.
15. Pass a trusted development execution context with team and granted scopes;
    missing required scopes deny dispatch without claiming production auth.
16. Revalidate and lock exact tool eligibility when authorizing dispatch. The
    committed `RUNNING` invocation is the linearization point: a disable that
    wins first blocks dispatch; a later disable affects future invocations.
17. Never hold a database transaction across context summarization, model calls,
    or tool calls.

## Resolved contracts

### Framework and state ownership

- Only `switchboard.adapters.orchestration` may import LangGraph.
- Domain and application code use provider/framework-independent frozen
  contracts and protocols.
- LangGraph state contains typed JSON-compatible values and durable identifiers,
  not SQLAlchemy sessions, repositories, mutable domain entities, or provider
  message/tool-call objects.
- The graph is ephemeral for one explicit run. Switchboard PostgreSQL records
  are the only durable authority; LangGraph checkpointing and thread memory are
  deferred to the Day 9 resume design.
- The base dependency is constrained to `langgraph>=1.2,<2` and locked by `uv`.

### Bounded graph

The only supported paths are:

```text
model action
├── Respond ────────────────────────────────→ finish
└── CallTool → durable read-only dispatch
               → final model Respond ──────→ finish
```

The first model action is exactly `Respond` or `CallTool`. The final model action
must be `Respond`; another tool request is rejected. The graph permits zero or
one tool call, no parallel branches, and uses an explicit low recursion limit in
addition to application-level action validation.

### Provider-independent boundaries

- `ModelGateway` accepts a bounded normalized request and returns `Respond` or
  `CallTool`.
- `AgentOrchestrator` accepts bounded context plus eligible tool descriptors and
  returns final response text plus whether a tool was called. Durable invocation
  identity remains owned by the application callback.
- `ToolCallHandler` is the callback through which the adapter requests one
  durable invocation. It validates and persists before calling an installed
  adapter; LangGraph never dispatches a tool directly.
- A deterministic fake model gateway provides direct, tool, malformed, and
  failure behavior without credentials or network access.

### Tool invocation lifecycle

```text
PENDING → RUNNING → SUCCEEDED
                  └→ FAILED
```

One invocation records its logical identity, owning turn and attempt, invocation
number, exact tool definition/version, canonical arguments, stable
platform-generated key, required/authorized scope snapshot, lifecycle
timestamps, and normalized result or safe failure code. Day 7 permits at most
one invocation per attempt.

`PENDING` commits before external dispatch. A locked final eligibility check,
`RUNNING` transition, and `tool.started` event commit atomically. The tool call
runs without an open transaction. Normalized success or failure is then
committed with its corresponding safe event.

### Public event contract

Add only:

- `tool.started` — stable invocation and exact tool identifiers;
- `tool.completed` — stable invocation identifier;
- `tool.failed` — stable invocation identifier and safe failure code.

No event contains arguments, tool output, prompts, provider exceptions, or
private reasoning. Direct execution remains `turn.started`, zero or more
`response.delta`, and `turn.completed`. Tool execution inserts the tool events
before response deltas. Every failure closes with the existing single
`turn.failed` terminal event.

### Completion and failure

- Reuse the existing response chunker and locked event sequence allocation.
- Final assistant-message insertion, turn/attempt success, and
  `turn.completed` commit atomically.
- Failure after start preserves committed invocation progress and records a safe
  durable turn/attempt failure.
- The existing coarse `RECEIVED`, `RUNNING`, and terminal turn statuses remain
  sufficient. Routing, approval, and fine-grained orchestration states are not
  added on Day 7.

## Build checkpoints

### Checkpoint 0 — Orchestration boundary design

Completed:

- inspected the current official `StateGraph`, async invocation, recursion, and
  checkpointer APIs;
- selected the base package without LangChain/provider dependencies;
- mapped direct and single-read-only-tool paths and termination;
- froze framework ownership, normalized ports, invocation lifecycle, event
  payloads, transaction boundaries, scope handling, and disable/dispatch
  concurrency semantics;
- added an architecture guard allowing LangGraph imports only in the
  orchestration adapter;
- confirmed no ADR is required because this applies the existing modular
  monolith/adapter decision rather than changing it.

### Checkpoint 1 — Model and orchestration ports

Add provider-independent model request/action/chunk/result and orchestration
request/result contracts, deterministic fake gateway, and malformed-output/
step-limit errors. Domain/application must not import LangGraph/provider SDKs.

### Checkpoint 2 — Tool-invocation domain and persistence

Add logical invocation ID, turn/attempt/tool ownership, canonical arguments,
stable key, lifecycle, normalized result/failure, timestamps, migration,
repositories, translators, constraints, focused updates, and redaction.

### Checkpoint 3 — Durable read-only dispatch service

Validate the exact selected tool and canonical arguments, require read-only
effect and granted scopes, lock/revalidate eligibility, persist before dispatch,
invoke under the manifest timeout without an open transaction, validate
normalized output, and persist safe success/failure.

### Checkpoint 4 — LangGraph adapter

Build bounded nodes:

1. request model action;
2. respond directly or validate one read-only tool;
3. invoke normalized adapter;
4. request final response from normalized result;
5. return structured output.

Graph state contains safe references/data, not sessions or domain entities tied
to the framework.

### Checkpoint 5 — Durable run-turn workflow

Load and compare-and-set start turn/attempt, build bounded context, load eligible
tools, run adapter, persist invocation before/after dispatch, emit committed
events, atomically append final message/terminal success, record durable failure,
and never execute a disallowed effect.

### Checkpoint 6 — Contract, concurrency, and end-to-end tests

Prove direct response, `search_work_items`, argument validation, disabled/
unbound/mutating rejection, malformed action, loop bound, SSE replay,
persistence across sessions, duplicate-start exclusion, missing scopes, timeout,
invalid output, safe partial failure, and deterministic fixtures.

### Checkpoint 7 — Documentation and verification

Update architecture, domain, event catalog, requirements evidence, operations,
`PROGRESS.md`, and this plan. Record debt: explicit runner, fake provider, one
tool, read-only only, no semantic router.

## Required tests

- Unit/application: actions, direct/tool path, limits, effect rejection,
  validation, stable identity, normalized outcomes.
- Adapter: deterministic graph transitions, exactly zero/one tool calls,
  malformed/failure paths, no sessions in graph state.
- PostgreSQL: invocation ownership/lifecycle, atomic completion, partial failure,
  disabled-before-dispatch, duplicate start rejection.
- E2E: API-created turn, explicit run, SSE tool/output events, final history.
- Architecture: only adapter imports LangGraph; concrete models/tools stay out of
  domain/application.

## Migration impact

Add `tool_invocations` with relational ownership, lifecycle/timestamp checks,
one invocation per attempt for Day 7, canonical JSON arguments/results, and
focused lifecycle updates. Extend the execution-event kind constraint for the
three public tool events. Do not add LangGraph checkpoint tables, outbox/claim
tables, approval records, or new coarse turn statuses.

## Public contract impact

Do not add an HTTP execution endpoint and do not change Day 6 command/read DTOs.
API acceptance still returns `202` without starting execution. After an explicit
run, the existing history endpoint exposes the final assistant message and the
existing SSE endpoint may deliver the three new stable tool event kinds while
preserving ordering and exclusive reconnect cursors.

## Security and safety considerations

Model-selected tools/arguments and tool output are untrusted. Only active,
bound, scoped, read-only tools execute. Final dispatch rechecks the exact pinned
version under lock. Redact sensitive values from events/logs/errors, bound
steps, text, candidates, arguments, timeout, and payloads, validate tool output,
and never persist chain-of-thought. Malicious tool output is data only and
cannot request a second tool or expand authority.

## Out of scope

Mutations, approval/policy decisions, semantic retrieval/confidence, multiple
tools, dynamic replanning, real provider credentials, durable outbox/worker
claiming/recovery, LangGraph checkpointing, unknown-outcome reconciliation,
tool retries, public execution endpoints, production actor authentication,
parallel branches, and provider streaming objects.

## Definition of done

- [x] LangGraph is isolated to an adapter.
- [x] Direct and one read-only tool path work.
- [x] Context/tools are pinned, eligible, and reproducible.
- [x] Invocation is durable with a stable idempotency key.
- [x] Mutating tools cannot execute.
- [x] Graph is bounded.
- [x] Final message and terminal success are atomic.
- [x] Events are safe/stable.
- [x] All adapter, persistence, E2E, architecture, and quality gates pass.
- [x] Documentation and `PROGRESS.md` are accurate.

## Completion evidence

- `uv run ruff format .` — 151 files unchanged;
- `uv run ruff check .` — clean;
- `uv run mypy` — clean across 84 source files;
- `uv run pytest` — 338 passed;
- `uv run pytest tests/integration -q` — 83 passed;
- Alembic head upgrade, one-revision downgrade, and re-upgrade — clean;
- `wsl docker build --tag switchboard:local .` — clean (native PowerShell
  `docker` was unavailable, so the documented build ran through WSL).

## Suggested commit

`feat(orchestration): add bounded LangGraph read-only agent loop`

## Earn

You can show a real agent loop while explaining why LangGraph is an adapter and
how deterministic testing prevents framework/provider behavior becoming a black
box.

## Assumptions to revisit

Semantic routing will narrow candidates; Day 8 adds durable approval; Day 9 adds
persisted multi-step resume instead of ephemeral graph memory. The explicit
runner and trusted development scope context are not durable dispatch or
production identity. Agent versions still do not pin complete prompt/model/
orchestrator configuration, and invocation/result retention remains undefined.
