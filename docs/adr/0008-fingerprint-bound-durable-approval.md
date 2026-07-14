# ADR 0008 — Fingerprint-Bound Durable Approval

**Status:** Accepted

## Context

A model may propose a mutating tool invocation, but model output is untrusted and
cannot authorize execution. Approval by tool name alone is insufficient: the
arguments, requester, owning team, pinned agent and tool versions, effect, or
policy can change between proposal and dispatch. Approval must also survive
process restarts and concurrent API requests without keeping an in-process graph
or database transaction open.

The platform must distinguish permission to use a capability, the policy result
for an exact proposed action, human confirmation, and final execution. It must do
so without claiming exactly-once effects across an external system.

## Decision

Switchboard will use separate immutable policy evaluations and durable approval
requests.

- A pure policy evaluator returns `ALLOW`, `DENY`, or
  `REQUIRE_CONFIRMATION`. Elevated approval remains reserved.
- Day 8 allows valid read-only actions, requires confirmation for valid mutating
  actions, and denies external-side-effect and privileged actions.
- A confirmation request binds to fingerprint version `action-v1`: SHA-256 over
  canonical team, requester, agent version, tool definition/version, effect,
  environment, policy version, and arguments.
- The linked invocation owns canonical arguments. Approval records do not
  duplicate them. Public reads/events expose only a value-free safe action
  summary and fingerprint version; the digest remains internal audit and
  revalidation data.
- Approval follows `PENDING -> APPROVED|REJECTED|EXPIRED`, with
  `APPROVED -> CONSUMED|EXPIRED`. Expiry is enforced lazily, including when an
  approved request expires before consumption.
- Mutation execution pauses durably in an explicit awaiting-confirmation state;
  no process or transaction waits for a decision.
- Decision commands are protected by PostgreSQL command receipts and approval
  compare-and-set updates.
- Before dispatch, Switchboard locks and revalidates ownership, fingerprint,
  versions, lifecycle, binding, conformance, scopes, effect, and expiry.
- Approval consumption, invocation start, resumed execution state, and safe
  public events commit atomically before adapter dispatch.
- Rejection or expiry cancels execution and never dispatches the adapter.
- Development team and actor headers model required context but are not
  production authentication or authorization.

## Alternatives considered

### Approve only a tool name or invocation identifier

Rejected because changed arguments, versions, requester, or policy could reuse
authority granted for a different action.

### Keep approval in LangGraph or process memory

Rejected because approval may outlive a process or connection, and the
orchestration framework is not the platform's durable authority.

### Embed policy fields only in the approval row

Rejected because allowed and denied evaluations also require audit evidence,
and policy evaluation is a distinct fact from a human decision.

### Allow external side effects after ordinary confirmation

Rejected for Day 8 because approval does not resolve ambiguous outcomes or make
blind retries safe. Reconciliation capability is required first.

### Store argument values in the public summary

Rejected because current manifests do not provide a sufficient sensitivity
model. A value-free summary is safer and still identifies the action shape.

## Consequences

- PostgreSQL becomes the concurrency authority for approval and resume.
- Fingerprint or policy-version changes require a new approval.
- Public events and reads can explain the decision without exposing arguments.
- Paused workflows need new durable lifecycle states and terminal cancellation
  semantics.
- Approval APIs need explicit development actor context and idempotent commands.
- Concurrent resume can select one dispatch winner, but a crash after dispatch
  begins can still create an unknown outcome.
- Production identity, separation of duties, elevated approval, external-effect
  reconciliation, retention, and richer redaction remain deferred.

## Day 8 implementation reconciliation

The delivered implementation follows this decision with separate
`policy_evaluations` and `approval_requests`, an internal `action-v1` digest,
value-free public summaries, and explicit awaiting/cancelled execution states.
`POST /api/v1/approvals/{approval_id}/decisions` uses generalized PostgreSQL
command receipts and `X-Actor-ID`; `GET` performs lazy expiry. Approval
consumption, invocation/turn/attempt resume, and `tool.started` commit before the
adapter call. Rejection or expiry emits `approval.resolved` and terminal
`turn.cancelled` without dispatch.

Concurrency tests establish one decision winner and one logical dispatch
transition for the tested flow. This does not strengthen the ADR's guarantee
across a post-dispatch crash: unknown-outcome recovery and reconciliation remain
deferred under ADR-0002.
