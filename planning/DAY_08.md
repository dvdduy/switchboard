# Day 8 — Policy Guardrails and Durable Approval

**Status:** Planned
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

## Provisional accepted direction

1. Pure policy engine returns `ALLOW`, `DENY`, or `REQUIRE_CONFIRMATION`;
   elevated approval is reserved.
2. Inputs include team, actor, pinned versions, effect, scopes, environment, and
   canonical arguments.
3. Read-only may be allowed with valid scopes/ownership.
4. Mutating/external side effects require confirmation.
5. Privileged tools are denied today.
6. Persist immutable policy decisions and approval requests with actor, expiry,
   exact fingerprint, safe summary, and linked invocation.
7. Add durable `AWAITING_CONFIRMATION` or equivalent lifecycle.
8. Emit `approval.required` and `approval.resolved`.
9. Approve/reject commands are idempotent and compare-and-set.
10. Revalidate fingerprint, versions, lifecycle, scopes, and expiry before
    dispatch.
11. Consume approval once per logical invocation; concurrent resume cannot
    dispatch twice.
12. Local actor/team context is explicitly not production authentication.

## Design questions to resolve

1. Lifecycle, recommended `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`,
   `CONSUMED`.
2. Separate immutable policy records versus embedding in approval.
3. Canonical JSON/fingerprint version.
4. Lazy versus background expiry; lazy is sufficient.
5. Rejection outcome: cancel versus safe completed response.
6. Event validation for paused states.

## Build checkpoints

### Checkpoint 0 — Threat model and policy matrix

Cover read-only scopes, mutations, external/privileged effects, cross-team/
disabled tools, changed arguments, expired/rejected approval, and malicious
tool/model output. Define safe summaries/redaction first.

### Checkpoint 1 — Policy and fingerprint domain

Build policy context/decision/reason codes, deterministic evaluator, canonical
argument normalization, versioned fingerprint, safe action summary, and
unit/property tests proving key-order stability and meaningful-change
invalidation.

### Checkpoint 2 — Durable approval persistence

Add identifiers/entities, lifecycle/expiry transitions, repositories,
translators, migration, ownership constraints, one active approval per logical
invocation, compare-and-set decisions, and immutable audit fields.

### Checkpoint 3 — Pause orchestration and events

Read-only stays unchanged. A proposed mutation persists invocation, policy, and
approval; pauses before dispatch; emits safe `approval.required`; and never
holds a process/transaction while waiting.

### Checkpoint 4 — Approval API and safe resume

Add versioned approve/reject commands and safe approval read. Validate actor/
team/idempotency, recheck fingerprint/version/lifecycle/scopes/expiry, consume
approval, dispatch one logical mutation, and persist final result/events. Rejected
or expired approval never dispatches.

### Checkpoint 5 — Concurrency and failure tests

Prove inert-before-approval, changed-argument rejection, expiry/rejection,
cross-team denial, idempotent repeated decision, approve/reject race, duplicate
resume protection, read-only bypass of confirmation, privileged denial,
malicious-text resistance, and durable audit.

### Checkpoint 6 — Documentation and verification

Update security/policy docs, architecture flow, domain/event catalog, use cases,
API examples, `PROGRESS.md`, and this plan. Add an ADR for fingerprint-bound
approval if needed. Run full migration/static/test/container/demo gates.

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

Elevated/admin approval, production RBAC/SSO, OAuth/delegation, batch approval,
notifications, unknown-outcome reconciliation, blind mutation retry,
compensation, semantic routing, and automatic durable dispatch.

## Definition of done

- [ ] Read-only execution requires valid ownership/scopes.
- [ ] Mutation is impossible before confirmation.
- [ ] Approval is durable, expiring, and fingerprint-bound.
- [ ] Changed arguments/versions invalidate approval.
- [ ] Decision/resume races are safe.
- [ ] One logical invocation dispatches at most once in tested flow.
- [ ] Privileged tools remain denied.
- [ ] Audit/events contain safe stable data.
- [ ] Migration, security, concurrency, API, E2E, and quality gates pass.
- [ ] Documentation and `PROGRESS.md` are accurate.

## Suggested commit

`feat(policy): add durable confirmation gate for mutations`

## Earn

You can demonstrate that a model cannot authorize a mutation, explain
fingerprint-bound compare-and-set approval, and prove the action is inert before
confirmation and executes once logically afterward.

## Assumptions to revisit

Production identity will replace fixtures; unknown outcomes need reconciliation;
elevated approval and richer policy bundles will expand the model.
