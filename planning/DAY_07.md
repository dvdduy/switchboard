# Day 7 — Framework-Isolated LangGraph Agent Loop

**Status:** Planned
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

## Provisional accepted direction

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

## Design questions to resolve

1. Minimum invocation lifecycle/outcome taxonomy.
2. Public event kinds for context/tool progress.
3. Response chunk integration with the existing producer.
4. Safe LangGraph state shape using durable IDs.
5. Whether coarse `TurnStatus` remains sufficient today.

## Build checkpoints

### Checkpoint 0 — Orchestration boundary design

Inspect current official LangGraph APIs, map direct/single-tool flows, define
termination and normalized model/tool contracts, separate durable versus
ephemeral state, and record an ADR if architecture changes.

### Checkpoint 1 — Model and orchestration ports

Add provider-independent model request/action/chunk/result and orchestration
request/result contracts, deterministic fake gateway, and malformed-output/
step-limit errors. Domain/application must not import LangGraph/provider SDKs.

### Checkpoint 2 — Tool-invocation domain and persistence

Add logical invocation ID, turn/attempt/tool ownership, canonical arguments,
stable key, lifecycle, normalized result/failure, timestamps, migration,
repositories, translators, constraints, focused updates, and redaction.

### Checkpoint 3 — LangGraph adapter

Build bounded nodes:

1. request model action;
2. respond directly or validate one read-only tool;
3. invoke normalized adapter;
4. request final response from normalized result;
5. return structured output.

Graph state contains safe references/data, not sessions or domain entities tied
to the framework.

### Checkpoint 4 — Durable run-turn workflow

Load and compare-and-set start turn/attempt, build bounded context, load eligible
tools, run adapter, persist invocation before/after dispatch, emit committed
events, atomically append final message/terminal success, record durable failure,
and never execute a disallowed effect.

### Checkpoint 5 — Direct and read-only end-to-end tests

Prove direct response, `search_work_items`, argument validation, disabled/
unbound/mutating rejection, malformed action, loop bound, SSE replay,
persistence across sessions, and deterministic fixtures.

### Checkpoint 6 — Documentation and verification

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

Expected `tool_invocations`, possibly new event kinds and coarse lifecycle
extensions.

## Security and safety considerations

Model-selected tools/arguments and tool output are untrusted. Only active,
bound, scoped, read-only tools execute. Redact sensitive values, bound steps,
tokens, timeout, and payloads, and never persist chain-of-thought.

## Out of scope

Mutations, approval, semantic retrieval/confidence, multiple tools, dynamic
replanning, real provider credentials, durable worker recovery, unknown-outcome
reconciliation, and parallel branches.

## Definition of done

- [ ] LangGraph is isolated to an adapter.
- [ ] Direct and one read-only tool path work.
- [ ] Context/tools are pinned, eligible, and reproducible.
- [ ] Invocation is durable with a stable idempotency key.
- [ ] Mutating tools cannot execute.
- [ ] Graph is bounded.
- [ ] Final message and terminal success are atomic.
- [ ] Events are safe/stable.
- [ ] All adapter, persistence, E2E, architecture, and quality gates pass.
- [ ] Documentation and `PROGRESS.md` are accurate.

## Suggested commit

`feat(orchestration): add bounded LangGraph read-only agent loop`

## Earn

You can show a real agent loop while explaining why LangGraph is an adapter and
how deterministic testing prevents framework/provider behavior becoming a black
box.

## Assumptions to revisit

Semantic routing will narrow candidates; Day 8 adds durable approval; Day 9 adds
persisted multi-step resume instead of ephemeral graph memory.
