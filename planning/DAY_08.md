# Day 8 — Policy Guardrails and Durable Approval

**Status:** Complete
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Day 7 complete

## Goal

Make execution read-only by default and add a durable confirmation boundary for
mutations. A proposed mutation remains inert until a valid, unexpired approval
matches the exact tool version, canonical arguments, actor/team context, and
policy decision.

## Learn

- Authorization, policy, confirmation, and execution are distinct.
- Approval binds to an argument fingerprint, not merely a tool name.
- Durable pause/resume needs lifecycle states and race handling.
- Approval does not make unsafe retries safe.
- Tool/model text is untrusted and cannot grant permission.
- Audit records identify requester, approver, exact action, and outcome.

## Why this matters

This implements the Phase 1 core of FR-030 through FR-034 and UC-04. It proves a
model cannot directly authorize a mutation.

## Current context

Expected prerequisites are versioned effects, a bounded one-tool loop, durable
invocations/keys, public API/events, and reference read-only/mutating tools. No
approval records, production identity, or unknown-outcome reconciliation exist.

## Accepted direction

1. A pure policy engine returns `ALLOW`, `DENY`, or `REQUIRE_CONFIRMATION`.
   `REQUIRE_ELEVATED_APPROVAL` remains a reserved target value and is not
   produced by the Day 8 evaluator.
2. Policy inputs are trusted platform context: team, requesting actor, pinned
   agent and tool versions, manifest effect and required scopes, granted scopes,
   environment, and canonical arguments. Model text and tool output are never
   authority inputs.
3. `READ_ONLY` is allowed only when ownership, binding, lifecycle, conformance,
   and scopes are valid. `MUTATING` requires confirmation after those checks.
   `EXTERNAL_SIDE_EFFECT` and `PRIVILEGED` are denied until reconciliation and
   elevated-approval capabilities exist.
4. Persist immutable policy evaluations separately from approval requests so
   allowed, denied, and confirmation-required decisions all retain audit
   evidence.
5. Approval binds to a versioned SHA-256 action fingerprint covering team,
   requester, agent version, tool definition and version, effect, environment,
   policy version, and canonical arguments.
6. Safe summaries contain stable tool identity, effect, and argument field names
   only. Argument values are omitted because manifests do not yet provide a
   trustworthy field-level sensitivity model.
7. A mutation creates a durable invocation, policy evaluation, and approval,
   enters an explicit `AWAITING_CONFIRMATION` execution state, emits
   `approval.required`, and returns without dispatch or a held transaction.
8. Approval lifecycle is `PENDING -> APPROVED|REJECTED|EXPIRED`, with
   `APPROVED -> CONSUMED|EXPIRED`. Expiry is enforced lazily by
   decision/read/resume workflows; no background expiry job is required.
9. Approve/reject commands require team, development actor, and idempotency key.
   They use command receipts for request replay and compare-and-set the approval
   row. Same-decision replay is stable; an opposite terminal decision conflicts.
10. Resume locks and revalidates ownership, fingerprint, pinned versions,
    lifecycle, binding, conformance, scopes, effect, and expiry. Approval
    consumption, invocation start, resumed execution lifecycle, and safe events
    commit atomically before adapter dispatch.
11. Rejection or expiry cancels the execution, emits `approval.resolved` and a
    stable terminal `turn.cancelled` event, and never invokes the adapter.
12. One approval can start one logical invocation. Database compare-and-set
    prevents duplicate concurrent resume in tested flows, but approval does not
    guarantee exactly-once effects after crashes or ambiguous external outcomes.
13. `X-Team-ID` and the new development actor context are trusted local fixtures,
    not production authentication, membership proof, or delegated authority.

## Resolved contracts

### Threat model and trust boundary

Trusted enforcement inputs come from Switchboard-owned records and application
context: team and actor identifiers, conversation ownership, pinned versions,
tool binding and lifecycle state, manifest effect/scopes, granted scopes,
environment, policy version, clock, and canonicalized arguments after schema
validation. The API, policy evaluator, approval workflow, executor, and
PostgreSQL constraints form the enforcement boundary.

User prompts, model actions, tool descriptions, schema annotations, retrieved
content, tool output, error text, and client-provided summaries are untrusted.
They may propose data but cannot grant scopes, choose an effect class, change a
policy result, approve an action, extend expiry, or trigger resume.

The required threat cases are cross-team identifiers, missing scopes, disabled
or unbound versions, changed arguments or versions, expired/rejected approvals,
approve/reject and duplicate-resume races, malicious instructions in model/tool
text, unsafe summaries, and a disable racing final dispatch authorization.

### Day 8 policy matrix

| Preconditions | Effect | Decision | Execution behavior |
|---|---|---|---|
| Wrong team, missing scope, unbound, inactive, or nonconformant | Any | `DENY` | No approval and no dispatch |
| Valid ownership, binding, lifecycle, conformance, and scopes | `READ_ONLY` | `ALLOW` | Existing locked dispatch path |
| Valid ownership, binding, lifecycle, conformance, and scopes | `MUTATING` | `REQUIRE_CONFIRMATION` | Persist and pause before dispatch |
| Otherwise valid | `EXTERNAL_SIDE_EFFECT` | `DENY` | Deferred until reconciliation exists |
| Otherwise valid | `PRIVILEGED` | `DENY` | Deferred until elevated approval exists |

Policy reason codes are stable machine-readable values. Human-readable model or
tool text is not stored as a policy reason.

### Development actor contract

Day 8 introduces a strongly typed `ActorId`. Explicit execution receives a
trusted requesting actor alongside the existing team and granted scopes.
Approval read and decision endpoints require `X-Team-ID`; decision commands also
require `X-Actor-ID` and `Idempotency-Key`. The requester and decision actor are
stored independently. Day 8 permits self-confirmation because production roles
and separation-of-duty policy are deferred, but never describes these headers
as authentication.

### Fingerprint and summary contract

Fingerprint version `action-v1` is SHA-256 over the existing deterministic
canonical JSON encoding of this logical envelope:

```json
{
  "actor_id": "<requester UUID>",
  "agent_version_id": "<UUID>",
  "arguments": {},
  "effect": "mutating",
  "environment": "development",
  "policy_version": "day8-v1",
  "team_id": "<UUID>",
  "tool_definition_id": "<UUID>",
  "tool_version_id": "<UUID>"
}
```

Object-key order is insignificant; array order, scalar type/value, identifiers,
effect, environment, and policy version are significant. The approval stores
only the fingerprint version and digest plus a separately derived safe summary.
It does not duplicate raw arguments. The linked invocation remains the durable
source of canonical arguments.

The Day 8 summary contains tool definition/version identifiers, effect, and
sorted argument field names. It contains no argument values, prompt/model text,
tool output, schema descriptions, exceptions, secrets, or private reasoning.

### Durable lifecycle and events

```text
policy ALLOW:                 PENDING -> RUNNING -> SUCCEEDED|FAILED
policy REQUIRE_CONFIRMATION: PENDING -> AWAITING_CONFIRMATION
                                            |-- approval consumed -> RUNNING
                                            |                         |
                                            |                         `-> SUCCEEDED|FAILED
                                            `-- rejection/expiry -> CANCELLED
```

The turn and current attempt also expose an explicit awaiting-confirmation state
so a paused durable workflow is not reported as actively running. No process or
database transaction remains open while awaiting a decision.

`approval.required` exposes approval, invocation, and exact tool identifiers,
expiry, fingerprint version, and the safe summary. The digest remains internal
audit/revalidation data because exposing it would permit equality correlation
and offline guessing of low-entropy arguments. `approval.resolved` adds only the
terminal decision and decision timestamp/actor identifier. It does not expose
arguments. Rejection or expiry appends `turn.cancelled`, which is a terminal SSE
event. Approval consumption and `tool.started` are committed in the same short
transaction; adapter work follows without an open transaction.

### Idempotency and guarantee boundary

Approval decision commands reuse PostgreSQL command receipts scoped by team,
operation, approval ID, and hashed idempotency key. Receipt fingerprints cover
the requested decision and actor. Approval-row compare-and-set remains the
authority across different idempotency keys and concurrent API replicas.

The Day 8 guarantee is: no mutation dispatch before a matching unexpired
approval, and at most one transition to dispatch for one logical invocation in
the tested concurrent flow. A crash after `RUNNING` commits may leave an
ambiguous outcome. Automatic claiming, delivery retries, `UNKNOWN_OUTCOME`, and
reconciliation remain deferred under ADR-0002.

## Build checkpoints

### Checkpoint 0 — Threat model and policy matrix

Completed:

- froze the trust boundary, threat cases, policy matrix, actor context, safe
  summary, and `action-v1` fingerprint envelope;
- selected separate immutable policy evaluations and durable approval requests;
- resolved approval and execution lifecycle, lazy expiry, cancellation, event,
  idempotency, revalidation, and concurrency semantics;
- denied external-side-effect and privileged execution until their stronger
  safety capabilities exist;
- documented the exact guarantee boundary and added ADR-0008.

### Checkpoint 1 — Policy and fingerprint domain

Completed:

- added strongly typed actor, environment, policy context, decision, stable
  reason-code, evaluation, fingerprint, and safe-summary contracts;
- implemented the deterministic Day 8 matrix with authorization/lifecycle
  denials taking precedence over tool effect;
- reused recursively frozen JSON and canonical serialization for normalized
  arguments and `action-v1` SHA-256 fingerprints;
- corrected the Checkpoint 0 envelope to include environment so cross-environment
  approval reuse is impossible;
- kept summaries value-free and proved malicious argument text cannot influence
  policy authority;
- added focused matrix, validation, immutability, key-order, meaningful-change,
  redaction, and version-consistency tests.

### Checkpoint 2 — Durable approval persistence

Completed:

- added policy-evaluation and approval identifiers plus immutable audit and
  fingerprint-bound approval entities;
- implemented pending, approved, rejected, expired, and consumed lifecycle
  validation, including lazy `APPROVED -> EXPIRED` handling at the exclusive
  expiry boundary;
- added repository ports, SQLAlchemy repositories/translators, unit-of-work
  wiring, row locking, and focused compare-and-set lifecycle updates;
- added one forward/backward Alembic migration with exact invocation ownership,
  team/tool ownership, evaluation/approval identity, lifecycle, fingerprint,
  effect, and one-active-approval constraints;
- stored the public safe summary as constrained tool/effect/argument-field data
  tied to the exact policy evaluation rather than as an opaque JSON object;
- proved round trips, immutable audit fields, stale and concurrent decision
  exclusion, active-approval uniqueness, ownership/fingerprint/summary
  rejection, migration round-trip, and metadata/schema agreement.

### Checkpoint 3 — Pause orchestration and events

Read-only stays unchanged. A proposed mutation persists invocation, policy, and
approval; pauses before dispatch; emits safe `approval.required`; and never
holds a process/transaction while waiting.

Completed:

- added nonterminal `awaiting_confirmation` lifecycle states for turns,
  attempts, and tool invocations, with explicit resume transitions;
- extended the durable execution-event contract with `approval.required` and
  migrated the relational lifecycle/event constraints forward and backward;
- made actor and environment explicit run inputs, evaluated every eligible tool
  call, and preserved the existing read-only dispatch path;
- persisted the mutating invocation, policy evaluation, pending approval,
  awaiting lifecycle transitions, and redacted approval event in one short
  transaction before returning a normalized pause outcome;
- ended orchestration immediately on that pause without a final model call,
  assistant message, adapter resolution, adapter dispatch, in-process wait, or
  open transaction;
- proved read-only regression behavior, domain pause/resume invariants,
  graph termination, safe event content, durable audit ownership, migration
  round-trip, metadata agreement, and the end-to-end mutation pause path.

### Checkpoint 4 — Approval API and safe resume

Add versioned approve/reject commands and safe approval read. Validate actor/
team/idempotency, recheck fingerprint/version/lifecycle/scopes/expiry, consume
approval, dispatch one logical mutation, and persist final result/events. Rejected
or expired approval never dispatches.

Completed:

- added `GET /api/v1/approvals/{approval_id}` and the strict, actor-bound,
  idempotent `POST /api/v1/approvals/{approval_id}/decisions` command;
- extended the existing command-receipt authority with approval-scoped results
  and request fingerprints covering team, approval, actor, and decision;
- exposed only requester/resolver identity, lifecycle timestamps, fingerprint
  version, and the value-free safe summary—never arguments or digest;
- revalidated durable ownership, pinned agent/tool identities, binding,
  activation/conformance, scopes, mutating effect, policy decision, lifecycle,
  environment, and exact action fingerprint before consumption;
- atomically consumed approval, resumed turn/attempt/invocation state, and
  appended `tool.started` before dispatching outside the transaction;
- persisted adapter success/failure and terminal execution events, while
  rejection or lazy expiry atomically cancelled the invocation, attempt, and
  turn with `approval.resolved` and terminal `turn.cancelled` events;
- added a forward/backward migration for generalized receipts, cancellation
  states, and approval-resolution events;
- proved safe reads, required team/actor headers, cross-team concealment,
  decision-key conflicts, stable replay with one adapter call, rejection with
  zero calls, expiry with zero calls, migration round-trip, and full regression.

### Checkpoint 5 — Concurrency and failure tests

Prove inert-before-approval, changed-argument rejection, expiry/rejection,
cross-team denial, idempotent repeated decision, approve/reject race, duplicate
resume protection, read-only bypass of confirmation, privileged denial,
malicious-text resistance, and durable audit.

Completed:

- proved a proposed mutation remains inert with no resolvable adapter and no
  `tool.started` event before valid approval consumption;
- proved changed durable arguments and a disabled pinned version fail final
  fingerprint/lifecycle revalidation without dispatch;
- proved rejection and exclusive-boundary expiry cancel the invocation,
  attempt, and turn with zero adapter calls;
- proved cross-team reads are concealed, actor headers are required, conflicting
  idempotency replays fail, and exact sequential replays are stable;
- proved concurrent identical decisions observe one atomic
  `CONSUMED`/`RUNNING`/`tool.started` boundary and make exactly one adapter call;
- proved an approve/reject race has one durable winner and duplicate resume
  cannot cross the dispatch transition twice;
- proved declared adapter failure after consumption is durably classified with
  safe tool/turn failure events;
- proved read-only execution still bypasses confirmation while external-effect
  and privileged proposals are denied with audit but without invocation;
- retained malicious-text/value redaction coverage and verified requester,
  resolver, policy fingerprint, command receipt, tool identity, lifecycle, and
  outcome remain durably auditable;
- proved approval events replay safely over SSE and conversation history never
  gains mutation arguments or tool output.

### Checkpoint 6 — Documentation and verification

Update security/policy docs, architecture flow, domain/event catalog, use cases,
API examples, `PROGRESS.md`, and this plan. Reconcile ADR-0008 with the delivered
implementation. Run full migration/static/test/container/demo gates.

Completed:

- reconciled the README, architecture, domain model, security/policy,
  requirements, use cases, operations, course, ADR-0008, and `PROGRESS.md` with
  the delivered policy and approval boundary;
- recorded Day 8 as complete and Day 9 as the next session without starting its
  implementation;
- passed Ruff format/check, strict mypy, 4 architecture tests, 98 integration
  tests, and the full 406-test suite;
- passed Alembic schema-drift validation and the focused three-test approval API
  demonstration;
- built the application image and verified its API import using Docker through
  WSL because the native Windows Docker CLI is unavailable.

## Required tests

- Unit/property: policy matrix, fingerprint, lifecycle/expiry, redaction,
  untrusted text.
- Application: pause, allow/deny, revalidation, rejection/expiry, one resume.
- PostgreSQL/concurrency: ownership, active approval uniqueness, decision race,
  duplicate resume, atomic consume/invocation transition.
- API/E2E: required event, safe details, contracts, SSE/history, invocation
  counter inert-before and once-after.

## Migration impact

Expected policy decision and approval tables plus lifecycle/event extensions.
Existing rows need safe mapping.

## Security and safety considerations

- Development identity is not production authentication.
- Approval applies only to exact fingerprints and pinned versions.
- Changed arguments require new approval.
- Summaries redact sensitive values.
- Tool/model content cannot alter scopes/policy.
- Privileged tools remain denied.
- Approvals expire.
- Every mutation records requester, decision actor, policy, tool, fingerprint,
  idempotency key, and outcome.
- Never log secrets or hidden reasoning.

## Out of scope

Elevated/admin approval, external-side-effect or privileged execution,
production RBAC/SSO, OAuth/delegation, separation-of-duty policy, batch approval,
notifications, unknown-outcome reconciliation, blind mutation retry,
compensation, semantic routing, and automatic durable dispatch.

## Definition of done

- [x] Read-only execution requires valid ownership/scopes.
- [x] Mutation is impossible before confirmation.
- [x] Approval is durable, expiring, and fingerprint-bound.
- [x] Changed arguments/versions invalidate approval.
- [x] Decision/resume races are safe.
- [x] One logical invocation dispatches at most once in tested flow.
- [x] Privileged tools remain denied.
- [x] Audit/events contain safe stable data.
- [x] Migration, security, concurrency, API, E2E, and quality gates pass.
- [x] Documentation and `PROGRESS.md` are accurate.

## Suggested commit

`feat(policy): add durable confirmation gate for mutations`

## Earn

You can demonstrate that a model cannot authorize a mutation, explain
fingerprint-bound compare-and-set approval, and prove the action is inert before
confirmation and executes once logically afterward.

## Assumptions to revisit

Production identity will replace fixtures; unknown outcomes need reconciliation;
elevated approval and richer policy bundles will expand the model.
