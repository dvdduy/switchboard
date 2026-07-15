# Day 9 — Durable Multi-Tool Pause and Resume

**Status:** Complete
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

Day 8 supplies bounded context, public APIs, active tools, a LangGraph adapter,
durable invocations, policy/approval, and explicit runner invocation. There is
no durable worker claiming/recovery or unknown-outcome reconciliation.

The current domain and PostgreSQL schema deliberately enforce the Day 7 bound
of exactly one invocation per attempt. Day 9 must remove that bound while
preserving ordered invocation identity and attempt ownership.

## Checkpoint 0 accepted design

ADR-0009 records the durable workflow boundary. The Day 9 reference is a
bounded, sequential, two-phase workflow:

1. persist one read-only discovery step for “find overdue critical tasks”;
2. dispatch that step only after its invocation intent is committed;
3. persist the normalized discovery result;
4. deterministically derive zero or more “move to Friday” mutations from that
   committed result;
5. atomically persist and freeze the ordered mutation plan, including exact
   invocation IDs, stable idempotency keys, arguments, policy evaluations, and
   a plan fingerprint;
6. pause durably for one approval covering the whole frozen mutation plan;
7. after approval, reconstruct progress from PostgreSQL in a new runner/UoW
   instance and execute the next incomplete mutation sequentially;
8. persist each result before selecting another mutation; and
9. create a final response that reports succeeded, failed, skipped, and
   uncertain operations truthfully.

The earlier phrase “persist the plan before any external step” was too broad:
exact mutations depend on discovery output. The precise invariant is that every
external action has durable immutable intent before its own dispatch. The
discovery step is persisted before search; the complete mutation plan is
persisted and frozen before approval or mutation dispatch.

### Aggregate and step boundary

- `TurnWorkflow` is framework-independent platform business state owned by one
  turn. Day 9 permits at most one workflow for a turn.
- `WorkflowStep` is an ordered typed relational record. Day 9 step kinds are
  `DISCOVERY_TOOL`, `MUTATION_TOOL`, and `FINAL_RESPONSE`.
- Steps form one bounded sequence. A step may reference only its immediate
  predecessor; general DAGs, cycles, and parallel readiness are invalid.
- Tool steps reference exact durable `ToolInvocation` records. The final step
  references the assistant message it creates.
- One turn attempt may own multiple positive, turn-ordered invocations. Day 9
  removes the Day 7 one-invocation-per-attempt restriction without redefining a
  physical attempt as a workflow step.
- Framework state may be rebuilt or cached, but it is never authoritative for
  plan contents, approval, progress, or completion.

### Plan and approval scope

- Discovery is the only executable step allowed before plan freeze.
- Discovery output is untrusted data. A deterministic validator, not tool text,
  selects bounded mutation templates and validates exact bound/active tools,
  arguments, effect, scopes, ownership, and duplicate targets.
- Once the mutation plan is frozen, no step may be inserted, removed, reordered,
  or have its executable fields changed.
- Each mutation retains its own `action-v1` policy fingerprint, invocation ID,
  and stable idempotency key.
- One plan-level approval uses `workflow-plan-v1`, covering team, requester,
  agent version, workflow and plan version, environment, policy version, and the
  ordered exact mutation action fingerprints. It exposes no digest or values.
- Approval makes the workflow resumable; it does not require an HTTP request to
  run an arbitrary multi-tool sequence. Existing Day 8 single-action approval
  behavior remains backward compatible.
- Clarification or changed intent creates a new user turn and workflow. Day 9
  does not mutate or version an already frozen plan in place.

### Lifecycle and failure policy

The workflow lifecycle is:

```text
DISCOVERY_PENDING -> DISCOVERY_RUNNING -> PLANNING
DISCOVERY_RUNNING -> DISCOVERY_FAILED
PLANNING -> AWAITING_CONFIRMATION | COMPLETING
AWAITING_CONFIRMATION -> RUNNING | CANCELLED
RUNNING -> COMPLETING
COMPLETING -> COMPLETED | FAILED | REVIEW_REQUIRED
```

Step lifecycle is `PENDING -> RUNNING -> SUCCEEDED|FAILED|UNKNOWN`, with
`PENDING -> SKIPPED` for mutations not attempted after stop-on-first-failure.
Completed terminal steps are immutable evidence.

- Rejection or expiry cancels before mutation dispatch.
- A known mutation failure stops further mutations, marks them skipped, and
  permits an honest final response before terminal failure.
- A post-dispatch result that cannot be established becomes `UNKNOWN`; later
  mutations are not dispatched, a deterministic value-free final response is
  created, and the workflow becomes `REVIEW_REQUIRED`.
- Day 9 does not reconcile or retry an unknown mutation. Its turn terminates
  with a safe failure event so SSE observers do not wait forever; the workflow
  record preserves the review-required fact for future reconciliation work.
- A `RUNNING` mutation found after process loss is conservatively uncertain
  because the persisted dispatch boundary cannot prove whether the adapter was
  called. It is never blindly reset to pending.

### Restart and concurrency boundaries

Safe recreation points are after committed workflow creation, discovery
completion, frozen-plan creation, approval resolution, and every terminal step
result. A new runner derives the next action solely from PostgreSQL and skips
successful steps.

PostgreSQL compare-and-set transitions select one discovery, mutation, and
finalization winner. This prevents duplicate concurrent logical dispatch in the
tested path, but it does not claim exactly-once external effects after a crash.
Production leases, automatic claiming, and reconciliation remain deferred.

### Safe event contract

Day 9 may add additive workflow events for planned, paused, resumed, and
terminal workflow state. Payloads contain only workflow/step IDs, positive step
numbers, step kinds, lifecycle values, counts, and safe failure codes. Existing
tool events remain the invocation evidence. Events never contain arguments,
search results, tool results, plan/action digests, prompts, exceptions, or
private reasoning.

### Proof that work was not repeated

Recovery and E2E tests will use adapter call counters plus persisted identities
to establish that:

- discovery is called once even when a new runner resumes after its result;
- completed mutations are skipped after recreation;
- every mutation dispatch uses its preallocated invocation ID/key;
- concurrent resumes select one logical dispatch winner;
- finalization creates one assistant message and terminal event; and
- an uncertain mutation prevents later calls and is not retried.

## Build checkpoints

### Checkpoint 0 — Reference workflow and invariants

Define the reference workflow, plan/dependencies, approval scope, failure
policy, events, restart points, and proof that completed steps are not repeated.

**Status:** Complete. Accepted above and in ADR-0009.

### Checkpoint 1 — Durable workflow domain and persistence

Build workflow/step IDs, lifecycle, typed definitions, plan version/fingerprint,
links to turn/invocation/approval/output, repositories, migration, constraints,
and translators.

Enforce positive order, unique identity/order, immediate-predecessor ownership,
terminal immutability, plan immutability after freeze, one workflow per turn,
and multiple ordered invocations per attempt.

**Status:** Complete. Added pure workflow/step lifecycles, repository/UoW ports,
SQLAlchemy persistence and translators, relational ownership/order/freeze
constraints, migration `d1a2b3c4d5e6`, and focused unit/PostgreSQL coverage.

### Checkpoint 2 — Durable discovery

Persist the workflow and discovery invocation before dispatch, execute search
once, persist its normalized result, and prove a recreated runner skips the
completed read.

**Status:** Complete. `RunWorkflowDiscovery` locks one turn/workflow authority,
persists invocation, policy evidence, workflow, and discovery step before
dispatch, revalidates exact eligibility at the start boundary, executes outside
transactions, and atomically records success or explicit discovery failure.
Recreated and concurrent runner tests prove committed reads are skipped and one
logical dispatch winner is selected.

### Checkpoint 3 — Frozen mutation plan and approval

Derive a bounded plan from committed discovery output. Validate active/bound/
owned tools and arguments, classify effects/scopes, allocate mutation
invocations and keys, compute action and plan fingerprints, persist policy
evidence, freeze the plan, create one exact-plan approval, and pause atomically.

Reject cycles, parallelism, excess steps, duplicate mutation targets, untrusted
step insertion, and plan changes after freeze.

**Status:** Complete. `FreezeWorkflowMutationPlan` reads only committed discovery
output, derives the reference due-date mutations through a bounded platform
template, validates the exact active/bound tool and every argument set, and
atomically persists ordered invocations, per-action policy evidence, a final
response step, the `workflow-plan-v1` fingerprint, one value-free plan approval,
the workflow/turn/attempt pause, and its safe event. A separate workflow-plan
approval aggregate/table preserves the Day 8 single-invocation approval contract.
Zero-result discovery freezes directly into completion without an approval.
Focused tests cover ordering and stable keys, redaction, invalid/duplicate input
rollback, zero mutations, and concurrent single-winner freeze.

### Checkpoint 4 — Resume after approval and recreation

Using a new runner, load workflow/approval/completed steps, verify fingerprint
and tool eligibility, skip successes, dispatch each mutation with its
preallocated key, persist each result before proceeding, prevent duplicate
workers, and atomically create the final summary/message/terminal event.

**Status:** Complete. `ApproveWorkflowPlan` records the plan-level human decision
without dispatching work, preserving the Day 8 single-action path.
`RunApprovedWorkflow` reconstructs the exact plan and authority from PostgreSQL,
recomputes every action and plan fingerprint, revalidates active/bound mutation
tools, consumes approval once, and claims one sequential step with committed
`RUNNING` state before each adapter call. It commits each successful result before
selecting another step, skips committed successes after runner recreation, and
atomically persists the final assistant message, final step, workflow, attempt,
turn, and terminal event. Workflow/turn locks and CAS transitions select one
concurrent dispatch/finalization winner. Timeout and ambiguous adapter boundaries
remain persisted as running evidence for Checkpoint 5's explicit uncertainty
policy rather than being mislabeled as known failures.

### Checkpoint 5 — Failure/interruption matrix

Cover interruption after workflow creation, after search, during approval,
after approval, between mutations, after the final tool result, duplicate
resume, rejected/expired approval, changed plan, disabled tool, and timeout or
unknown outcome.

Prove completed calls are not repeated and unknown outcomes do not blind-retry.

**Status:** Complete. Rejection and expiry now atomically resolve the plan
approval, cancel every never-dispatched invocation, skip remaining steps, cancel
the workflow/attempt/turn, and emit safe resolution and terminal events. Declared
adapter failures persist one known failed mutation, cancel and skip later work,
and produce a value-free truthful failed summary. Timeout, adapter exceptions,
invalid post-dispatch output, and explicitly recovered process interruption
persist `UNKNOWN` invocation/step evidence, skip later work, produce a
review-required summary, fail the public turn stream safely, and never retry the
ambiguous call. A dedicated migration `f3c4d5e6f7a8` adds the durable invocation
unknown state. Disabled tools become a known pre-dispatch terminal failure with
no adapter call. Focused tests cover rejection, expiry, post-approval resume,
changed plans, disabled tools, known failure, timeout/unknown outcome, recovery
from persisted running state, duplicate/concurrent runners, interruption between
mutations and after the final result, terminal replay, and adapter counters.

### Checkpoint 6 — Public contracts and multi-turn behavior

Add safe additive workflow events and approval fields without breaking Day 8
single-action behavior. Demonstrate a follow-up turn inspecting completed
workflow state; clarification/replanning in place remains deferred.

**Status:** Complete. The existing approval endpoints now identify invocation
versus workflow-plan targets additively and expose only ordered value-free plan
summaries. Day 8 response values and decision behavior remain compatible. Safe
`workflow.planned`, `workflow.resumed`, and `workflow.terminal` events provide
durable SSE observations without changing turn-stream terminal semantics. An
API-to-runner integration test proves plan approval, stable replay, redaction,
explicit execution, terminal event replay, and a later turn inspecting the
persisted workflow result. Public workflow creation, arbitrary execution, and
in-place clarification/replanning remain deferred.

### Checkpoint 7 — Documentation and verification

Update architecture diagrams, domain/use cases, events, security/recovery,
README demo, `PROGRESS.md`, and this plan. Reconcile ADR-0009 with the delivered
implementation. Run migration, static, recovery, concurrency, E2E, and
container gates.

**Status:** Complete. Architecture, domain, requirements, use cases, security,
operations, testing, README, ADR-0009, course, and progress documentation now
describe the delivered PostgreSQL-owned workflow boundary and deferred debt.
Ruff formatting/lint, strict mypy, 26 focused migration/restart/concurrency/
failure/API tests, and the full 458-test suite pass. Compose validation and the
container image build could not run because the execution environment has no
Docker CLI; no project-level container failure was observed.

## Required tests

- Unit/property: plan validation/cycles, fingerprint, step lifecycle, next step,
  completed skip, bounds, redaction.
- Application: persist-before-execute, pause/resume, no repeat read, stable keys,
  changed-plan invalidation, failure policy, truthful summary.
- PostgreSQL/concurrency: migration, ownership/order, one workflow per turn,
  compare-and-set claims, duplicate resume, restart boundaries, atomic finish.
- E2E: API conversation, explicit runner, approval API, SSE progress, final
  history, adapter counters proving no duplicate effects.

## Migration impact

Expected workflow and step tables, event kinds, plan-level approval linkage,
and removal of the Day 7 single-invocation constraints. Serialized LangGraph
state is not durable business state. Existing rows must remain valid.

## Public contract impact

Conversation and SSE routes remain stable. Approval and event DTOs may gain
additive workflow target/progress fields. No public workflow-management or
runner API is required. Existing single-action approval behavior remains
backward compatible.

## Concurrency risks

- duplicate plan materialization;
- duplicate approval or resume;
- two runners selecting the same step;
- plan mutation after approval;
- duplicate final assistant messages/events;
- invocation-number allocation races; and
- process loss after the dispatch boundary.

PostgreSQL uniqueness, row locks, focused updates, and compare-and-set lifecycle
transitions are the authorities. No transaction spans external work.

## Security and safety considerations

Every mutation is validated/fingerprinted before approval; plan changes
invalidate approval; tool/model output cannot insert executable steps; step,
argument, runtime, and mutation counts are bounded; events are redacted;
cross-team resources are forbidden; and unknown outcome stops safely.

## Out of scope

Parallel fan-out, arbitrary replanning after approval, compensation, production
worker leasing, automatic claiming, delivery-attempt retry modeling,
non-PostgreSQL locks, unknown-outcome reconciliation, review queue, semantic
router, real provider/production adapters, production identity, elevated or
external-effect approval, retention policy, and unbounded autonomous loops.

## Definition of done

- [ ] Plan/steps are durable before their external actions.
- [ ] Pause/resume works from a new process instance.
- [ ] Completed steps are not repeated.
- [ ] Mutations have stable logical IDs/keys.
- [ ] Approval covers exact ordered plan/arguments.
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
