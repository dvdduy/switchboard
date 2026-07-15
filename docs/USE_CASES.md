# Use Cases

## UC-01 — Register and activate a tool

**Primary actor:** Tool developer  
**Goal:** Make a product capability available to authorized agents without changing platform routing code.

### Preconditions

- The developer belongs to a team namespace.
- A callable tool endpoint or local adapter exists.

### Main flow

1. Developer submits a versioned tool manifest.
2. Switchboard validates identity, schemas, effect type, scopes, timeout, retry policy, ownership, and idempotency declaration.
3. The conformance suite runs representative valid, invalid, timeout, and error scenarios.
4. The published version remains `DRAFT` until a successful exact-version run
   is explicitly activated.
5. A product developer binds the active version to a new agent version.
6. Eligible routing requests may now consider it.

### Alternate flows

- Invalid schema: registration fails with field-level diagnostics.
- Missing owner or effect classification: activation is denied.
- Conformance failure: version remains `DRAFT`.
- Prior version exists: it remains available for pinned conversations until deprecated or disabled.

### Success evidence

A new reference tool validates, passes conformance, activates, binds to a new
agent version, and appears in the eligible registry result without modifying
orchestration or routing code. Semantic routing itself is not implemented yet.

---

## UC-02 — Answer a read-only request

**Primary actor:** End user  
**Example:** “Which Project Alpha tasks are overdue?”

**Implemented in Phase 1:** The client creates a conversation or continuation
through `/api/v1`. Switchboard atomically persists the user message, received
turn, pending attempt, and idempotency receipt, then returns `202` and a
reconnectable event URL. A trusted explicit runner builds the pinned bounded
context and gives active, bound, scoped descriptors to a deterministic
structured model. It can dispatch exact `search_work_items`, normalize the
result as untrusted data, generate a final response, and expose committed safe
tool/output events through history and SSE.

Automatic outbox dispatch, semantic routing/confidence, production identity and
health and real model generation in the target flow below are
not implemented. The numbered flow remains the target architecture rather than
a claim that every step is complete.

1. Client creates a turn.
2. API persists message, turn, event, and outbox record atomically.
3. Worker loads the pinned agent version and bounded context.
4. Eligible tools are filtered by binding, authorization, and health.
5. Router selects `search_work_items` with confidence and candidate evidence.
6. Policy returns `ALLOW` because the tool is read-only and scopes are valid.
7. Executor validates arguments and invokes the tool.
8. Model generates an answer from the normalized result.
9. Committed events and output stream to the client.
10. Trace records versions, latency, tokens, cost, and outcomes.

---

## UC-03 — Clarify an ambiguous request

**Example:** “Update the project.”

1. Router finds several plausible tools with no acceptable winner.
2. It returns `NEEDS_CLARIFICATION`, not a guessed call.
3. Agent asks a constrained question, such as whether the user means owner, status, or description.
4. The user response creates another turn and supplies the missing information.
5. The original ambiguity and clarification are visible in the trace and eval data.

**Invariant:** Ambiguity never silently becomes a mutating action.

---

## UC-04 — Confirm and execute a mutation

**Example:** “Move TASK-123’s due date to Friday.”

**Implemented Day 8 subset:** A deterministic structured model may propose one
active, bound, scoped `MUTATING` call. Switchboard atomically persists the exact
invocation, immutable policy evaluation, expiring fingerprint-bound approval,
awaiting execution state, and safe `approval.required` event. The versioned API
provides a safe read and actor-bound idempotent decision command. Approval
consumption and `tool.started` commit before the local adapter call; rejection
or expiry emits `approval.resolved` and terminal `turn.cancelled` without
dispatch.

1. Router selects `update_due_date`.
2. Policy evaluates user, team, scopes, tool effect, environment, and normalized arguments.
3. Policy returns `REQUIRE_CONFIRMATION`.
4. Switchboard creates a durable approval request containing a safe action summary and argument fingerprint.
5. Client presents the request.
6. User approves.
7. Switchboard verifies that approval is unexpired and still matches ownership,
   binding, lifecycle, scopes, versions, policy, and intended arguments.
8. Executor invokes the pinned tool version with a stable idempotency key.
9. Result, audit event, and response are persisted.

### Alternate flows

- Rejection or expiry: turn becomes cancelled or responds without executing.
- Arguments change after approval: old approval is invalid; a new approval is required.
- Missing scope: policy denies before confirmation.

**Guarantee boundary:** tested concurrent flows cross the logical dispatch
transition once. A crash after `RUNNING` commits can still leave an ambiguous
external outcome; automatic recovery and reconciliation are deferred.

---

## UC-05 — Resume a multi-tool workflow

**Example:** “Find overdue critical tasks, move them to Friday, and summarize the changes.”

**Implemented Phase 1 subset:** A trusted explicit runner persists and executes
one read-only discovery invocation, then a deterministic platform validator
derives at most ten exact ordered mutations from its committed normalized
result. The entire mutation plan, stable invocation keys, policy evidence,
`workflow-plan-v1` fingerprint, value-free approval, and pause commit before any
mutation dispatch. The existing approval API reads and decides the plan safely.
A recreated runner recomputes authority from PostgreSQL, consumes approval,
skips completed steps, dispatches one mutation at a time, and persists every
terminal result before selecting another.

1. Search tool returns candidate tasks.
2. Agent builds a proposed mutation plan.
3. Workflow pauses for approval.
4. Process or worker may restart while paused.
5. After approval, workflow resumes from persisted state.
6. Each logical update has its own stable invocation key.
7. Final response summarizes succeeded, failed, and uncertain operations.

**Invariant:** Completed search and approved mutations are not repeated merely because orchestration resumes.

Known failure stops and skips later mutations. Timeout, adapter exception,
invalid post-dispatch output, or explicit interrupted-run recovery records an
unknown invocation/step outcome, stops later dispatch, creates a value-free
review summary, and never blindly retries. Rejection/expiry cancels the frozen
plan before any mutation dispatch. A follow-up turn can inspect the durable
assistant summary through ordinary bounded conversation context.

**Deferred:** automatic worker claiming, a public workflow runner, in-place
replanning, parallel branches, compensation, and unknown-outcome reconciliation.

Day 10 composes UC-02, UC-04, and UC-05 into deterministic external-client
demonstrations. It also executes a failure matrix proving that disconnect,
replay, malformed output, disabled tools, approval changes, rollback, duplicate
delivery, and unknown mutation outcomes preserve these documented boundaries.

---

## UC-06 — Recover from a worker crash before tool dispatch

1. Turn and outbox record are committed.
2. Worker claims the job and crashes before invoking the tool.
3. Claim lease expires or recovery logic reclaims the work.
4. A new worker resumes from the persisted state.
5. No external side effect has occurred, so execution proceeds safely.

---

## UC-07 — Reconcile an unknown mutation outcome

1. Executor dispatches `create_work_item` with idempotency key `K`.
2. External service commits the item.
3. Network response is lost and the call times out.
4. Invocation becomes `UNKNOWN_OUTCOME`.
5. Switchboard does not issue a blind retry.
6. Reconciliation queries the tool by `K` or another stable operation reference.
7. Existing item is found.
8. Original invocation is marked successful and execution resumes.

### Alternate flows

- Reconciliation proves no effect: safe retry reuses `K`.
- Reconciliation cannot determine the result: route to review and do not duplicate the mutation.

---

## UC-08 — Reconnect to a response stream

1. Client receives events 1–14 and disconnects.
2. Worker continues according to execution semantics.
3. Client reconnects with `Last-Event-ID: 14`.
4. API replays committed events starting at 15 and follows live events.
5. Client deduplicates by event ID if necessary.

**Invariant:** Client transport interruption does not corrupt turn state.

---

## UC-09 — Block an offline regression

1. Engineer changes a router prompt, model, policy, or agent definition.
2. CI creates a candidate version and runs the pinned eval bundle.
3. Deterministic evaluators check tool choice, prohibited actions, confirmation, and schema compliance.
4. Calibrated judge evaluates designated open-ended cases.
5. Candidate is compared with the approved baseline.
6. Threshold violation fails CI with changed-case details.

---

## UC-10 — Shadow and canary a candidate

1. Candidate passes offline evaluation.
2. Shadow stage receives copies of eligible traffic but executes nothing.
3. Operators review selection differences, latency, and estimated cost.
4. Candidate advances to a small canary percentage.
5. Live signals are compared with the baseline.
6. Healthy candidate advances; unhealthy candidate automatically rolls back.

---

## UC-11 — Investigate a reported incident

**Example report:** “The assistant changed the wrong task.”

1. Operator opens the correlated turn trace.
2. Trace shows pinned versions, context references, candidate scores, selected tool, policy result, approval identity, argument fingerprint, tool outcome, and latency.
3. Operator replays stored events without re-executing the mutation.
4. Root cause is classified: routing, context, policy, approval UX, tool implementation, stale data, or user ambiguity.
5. A sanitized case may be promoted into a new eval dataset version.

---

## UC-12 — Disable an unhealthy tool

1. Tool health or failure metrics cross a configured threshold.
2. Tool version becomes unavailable or disabled.
3. New routing decisions exclude it.
4. Users receive a truthful availability fallback or an alternative tool if policy permits.
5. Existing historical traces remain tied to the disabled version.
