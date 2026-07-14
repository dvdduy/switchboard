# Day 9 — Durable Multi-Tool Pause and Resume

**Status:** Planned
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Day 8 complete

## Goal

Execute a bounded multi-step workflow that can call multiple tools, pause for
approval, survive process recreation, and resume from persisted progress without
repeating completed reads or approved mutations.

## Learn

- Durable orchestration is persisted progress, not a long-lived coroutine.
- Framework checkpoints and platform business state differ.
- Each logical side effect needs stable invocation identity/idempotency.
- Resume discovers the next incomplete safe step.
- Partial success, failure, and uncertainty are explicit.
- Dynamic replanning should be bounded.

## Why this matters

This implements the Phase 1 core of FR-023 and UC-05 and proves the model can
support a realistic workflow rather than only one tool call.

## Current context

Expected prerequisites are bounded context, public API, active tools, LangGraph
adapter, durable invocations, policy/approval, and explicit runner invocation.
There is no durable worker claiming/recovery or unknown-outcome reconciliation.

## Provisional accepted direction

1. Add framework-independent `TurnWorkflow` and ordered `WorkflowStep` state.
2. Initial sequential plan:
   - read-only search;
   - zero or more proposed mutations;
   - approval pause;
   - approved mutations;
   - final summary.
3. Persist the plan before any external step.
4. Completed steps are immutable evidence and skipped on resume.
5. Every mutation gets a stable invocation ID/key before approval/dispatch.
6. Approval covers exact canonical planned mutations with a plan fingerprint;
   each invocation retains its own key.
7. Resume reconstructs from PostgreSQL. LangGraph checkpoints are optional, not
   sole truth.
8. Restart is simulated with a new runner/UoW instance.
9. Sequential bounded steps; no insertion after approval.
10. Final response distinguishes success, failure, skip, and uncertainty.
11. Unknown mutation outcome stops for review and is never blindly retried.

## Design questions to resolve

1. Generic versus typed step representation; prefer typed values plus relational
   steps.
2. Exact plan fingerprint and approval granularity.
3. How clarification creates a new plan version and invalidates approval.
4. Whether graph checkpoint state is separate or rebuilt; rebuild-first.
5. Stop-on-first-failure versus continue-independent; start stop-on-failure.
6. Safe public workflow events.

## Build checkpoints

### Checkpoint 0 — Reference workflow and invariants

Define: find overdue critical tasks, propose Friday changes, approve, update, and
summarize. Document plan/dependencies, approval scope, failure policy, events,
restart points, and proof that completed steps were not repeated.

### Checkpoint 1 — Durable workflow domain and persistence

Build workflow/step IDs, lifecycle, typed definitions, plan version/fingerprint,
links to turn/invocation/approval/output, repositories, migration, constraints,
and translators.

Enforce positive order, unique identity/order, internal dependencies, terminal
immutability, plan immutability after start, and one active workflow per turn.

### Checkpoint 2 — Plan creation

Produce a validated bounded plan from deterministic planner output. Validate
active/bound/owned tools and arguments, classify effects/scopes, allocate
invocations, compute fingerprints, and persist the entire plan atomically.

Reject cycles, parallelism, excess steps, duplicate mutations, and plan changes
after approval.

### Checkpoint 3 — Sequential executor and pause

Load next executable step, compare-and-set claim it, execute read-only search
once, persist output, create exact-plan approval, pause without live process/
transaction state, emit safe workflow events, and return resumable outcome.

### Checkpoint 4 — Resume after approval and recreation

Using a new runner, load workflow/approval/completed steps, verify fingerprint
and tool eligibility, skip successes, dispatch each mutation with preallocated
key, persist each result before proceeding, prevent duplicate workers, and
atomically create final summary/message/terminal event.

### Checkpoint 5 — Failure/interruption matrix

Cover interruption after plan, after search, during approval, after approval,
between mutations, after final tool result, duplicate resume, rejected/expired
approval, changed plan, disabled tool, and timeout/unknown outcome.

Prove completed calls are not repeated and unknown outcomes do not blind-retry.

### Checkpoint 6 — Multi-turn behavior

Demonstrate one honest conversation continuation: clarification followed by a
new resolved turn, or a follow-up turn inspecting completed workflow state.
Record the chosen interpretation.

### Checkpoint 7 — Documentation and verification

Update architecture diagrams, domain/use cases, events, security/recovery,
README demo, `PROGRESS.md`, and this plan. Add durable-workflow ADR if needed.
Run migration, static, recovery, concurrency, E2E, and container gates.

## Required tests

- Unit/property: plan validation/cycles, fingerprint, step lifecycle, next step,
  completed skip, bounds, redaction.
- Application: persist-before-execute, pause/resume, no repeat read, stable keys,
  changed-plan invalidation, failure policy, truthful summary.
- PostgreSQL/concurrency: migration, ownership/order, one active workflow,
  compare-and-set claims, duplicate resume, restart boundaries, atomic finish.
- E2E: API conversation, explicit runner, approval API, SSE progress, final
  history, adapter counters proving no duplicate effects.

## Migration impact

Expected workflow and step tables, event kinds, and perhaps plan fingerprint/
version links on approval or invocation. Serialized LangGraph state is not the
only durable business state.

## Security and safety considerations

Every mutation is validated/fingerprinted before approval; plan changes
invalidate approval; tool/model output cannot insert executable steps; step/
argument/runtime/mutation counts are bounded; events are redacted; cross-team
resources are forbidden; unknown outcome stops safely.

## Out of scope

Parallel fan-out, arbitrary replanning after approval, compensation, production
worker leasing, non-PostgreSQL locks, unknown-outcome reconciliation, review
queue, semantic router, and unbounded autonomous loops.

## Definition of done

- [ ] Plan/steps are durable before execution.
- [ ] Pause/resume works from a new process instance.
- [ ] Completed steps are not repeated.
- [ ] Mutations have stable logical IDs/keys.
- [ ] Approval covers exact plan/arguments.
- [ ] Duplicate resume cannot double-dispatch in tests.
- [ ] Unknown outcomes are not blindly retried.
- [ ] Final response accurately summarizes outcomes.
- [ ] Multi-turn state is demonstrated honestly.
- [ ] Migration, recovery, concurrency, E2E, architecture, and full gates pass.
- [ ] Documentation and `PROGRESS.md` are accurate.

## Suggested commit

`feat(workflows): add durable multi-tool pause and resume`

## Earn

You can demonstrate durable orchestration: persisted plans, restart-safe resume,
stable invocation identity, approval-bound mutations, and evidence that
completed external actions are not replayed.

## Assumptions to revisit

Phase 2 worker claiming will automate resume; reconciliation will handle unknown
outcomes; semantic routing/dynamic planning will replace deterministic fixtures
in bounded ways.
