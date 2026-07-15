# ADR 0009 — PostgreSQL-Owned Durable Workflow Progress

**Status:** Accepted

## Context

Day 8 can execute a direct response or one tool invocation and can durably pause
one mutation for fingerprint-bound approval. Day 9 must support a realistic
workflow that discovers candidate work, derives several exact mutations, pauses
for approval, survives process recreation, and resumes without repeating
completed actions.

A long-lived coroutine or serialized LangGraph checkpoint cannot be the durable
business authority. It may disappear with a process, encode framework-specific
implementation details, or disagree with already committed tool effects.

The reference workflow also cannot persist its complete mutation plan before
discovery: exact mutation arguments depend on the committed search result. The
platform needs a durable boundary for both discovery intent and the later frozen
mutation plan.

## Decision

Switchboard will persist framework-independent workflow progress in PostgreSQL.

- One `TurnWorkflow` belongs to one logical turn for Day 9.
- Ordered typed `WorkflowStep` records represent discovery tools, mutation
  tools, and final response creation.
- The workflow is bounded and sequential. General DAGs, parallel execution, and
  dynamic insertion are not supported.
- Discovery intent and its exact invocation are committed before search
  dispatch.
- A known discovery failure terminates explicitly as `DISCOVERY_FAILED` before
  any mutation plan or approval exists.
- After discovery succeeds, Switchboard derives mutations through trusted
  deterministic validation of the committed result.
- The complete ordered mutation plan, exact invocations, stable idempotency
  keys, policy evaluations, and fingerprints commit atomically before approval.
- Plan freeze makes executable step content immutable. Replanning or
  clarification creates a new user turn and workflow.
- One plan-level `workflow-plan-v1` approval covers the ordered exact mutation
  action fingerprints. Every mutation retains its own `action-v1` policy
  fingerprint and invocation key.
- Plan approval is distinct from workflow execution. The decision makes the
  workflow resumable; an explicit runner advances persisted progress. Existing
  Day 8 single-invocation approval remains supported.
- A new runner reconstructs the next safe action from PostgreSQL, skips
  successful steps, and persists each terminal result before continuing.
- PostgreSQL row locks, uniqueness constraints, and compare-and-set lifecycle
  transitions select one logical planning, dispatch, and finalization winner.
- No transaction remains open across model or tool adapter calls.
- A known mutation failure stops later mutations and marks them skipped. An
  uncertain post-dispatch result stops later mutations, produces a deterministic
  value-free final response, terminates the workflow in `REVIEW_REQUIRED`, and
  is never blindly retried.
- Uncertain dispatch persists `UNKNOWN` on both the invocation and workflow step.
  A live competing runner receives an in-progress conflict; converting a
  persisted `RUNNING` boundary to unknown requires an explicit recovery command
  after the caller has established process loss.
- LangGraph may coordinate a bounded in-process segment, but its checkpoint is
  optional derived state and never the sole source of workflow truth.

## Plan fingerprint

`workflow-plan-v1` is a SHA-256 digest over a canonical versioned value
containing:

- team and requester identity;
- pinned agent version;
- workflow identity and positive plan version;
- environment and policy version; and
- the ordered mutation entries, each containing its positive step number,
  stable invocation identity, and exact `action-v1` fingerprint.

The linked workflow, invocations, and policy evaluations remain the durable
sources for executable values. Approval records and public events do not copy
argument values or expose either digest.

## Restart guarantee

Day 9 guarantees deterministic resume at committed safe boundaries: after
workflow creation, discovery completion, frozen-plan creation, approval
resolution, and each terminal step result. Completed steps are immutable
evidence and are not selected again.

The guarantee does not claim exactly-once external effects. If a process is lost
after a mutation crosses the persisted dispatch boundary but before its result
commits, resume cannot prove whether the external operation occurred. The
mutation becomes uncertain and blocks later dispatch until future reconciliation
or review capability exists.

## Alternatives considered

### Persist only a LangGraph checkpoint

Rejected because framework state is not a stable public or business contract
and cannot replace durable invocation, approval, and effect evidence.

### Build the full mutation plan before discovery

Rejected because the reference mutation targets and arguments derive from
search results. Pretending otherwise would weaken the learning example.

### Append mutation steps incrementally after approval

Rejected because approval would not cover a fixed, reviewable action set and
untrusted output could expand authority after confirmation.

### Treat each workflow step as a new turn attempt

Rejected because an attempt represents physical execution of one logical turn,
whereas steps are business progress inside that execution. Conflating them
would obscure retries and recovery.

### Automatically retry every running mutation after restart

Rejected because `RUNNING` means the dispatch boundary was committed and the
external effect may already have occurred.

### Approve every mutation through separate user decisions

Rejected for the bounded reference workflow because it produces unnecessary
approval fatigue. One plan approval is safe when it binds the ordered exact
actions and each action retains separate policy and invocation evidence.

## Consequences

- The domain and schema gain explicit workflow and step state.
- Day 7's one-invocation-per-attempt constraint must be removed carefully while
  preserving positive turn-local invocation ordering and legacy rows.
- Approval persistence must support an exclusive workflow-plan target without
  weakening existing exact-invocation approval.
- The public event catalog gains only safe additive workflow progress.
- Recovery tests must recreate runners/UoWs and prove completed work is skipped.
- Post-dispatch crash recovery remains conservative until worker leasing and
  unknown-outcome reconciliation are implemented.
- Production outbox dispatch, worker claiming, parallel scheduling,
  compensation, dynamic replanning, and review queues remain deferred.

## Delivered Day 9 reconciliation

Checkpoints 1–6 implemented this decision without changing its authority
boundary. Migrations `d1a2b3c4d5e6` through `g4d5e6f7a8b9` add workflow/step
state, a separate workflow-plan approval, explicit unknown invocation outcome,
and safe workflow events. The application workflows persist discovery intent,
freeze exact mutations, decide/cancel the plan, reconstruct approved progress,
and conservatively recover an interrupted running mutation.

The delivered public surface intentionally reuses the approval and SSE routes
additively. It does not expose workflow creation or execution. Plan-decision
replay is lifecycle-idempotent; a generalized command receipt for cross-plan
idempotency-key authority remains deferred. Automated worker claims, leases,
unknown-outcome reconciliation, parallelism, compensation, and in-place
replanning also remain outside this ADR's implemented scope.
