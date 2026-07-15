# Operations

## Operational goals

- Know whether turns are completing correctly.
- Attribute slowness and failure to a stage.
- Prevent one team or tool from destabilizing the platform.
- Recover deterministically from worker and dependency failure.
- Release agent behavior gradually with measurable rollback criteria.

## Core service-level indicators

Per agent and release version:

- turn acceptance rate;
- turn completion rate;
- time to first committed stream event;
- total turn latency;
- queue wait time;
- routing latency;
- model latency;
- tool latency;
- fallback and clarification rate;
- policy denial and confirmation rate;
- tool timeout/error rate;
- unknown-outcome rate;
- tokens and estimated cost;
- SSE reconnect and replay count.

## Initial local SLO objectives

These are project objectives, not production promises:

- 99% of accepted simulated turns reach a terminal state.
- No accepted turn is silently lost after API acknowledgement.
- Zero duplicate logical mutation in the defined crash/retry scenario.
- All committed stream events remain replayable.
- Canary rollback occurs within 60 seconds of a sustained configured breach.

The current implementation does not yet substantiate the accepted-turn loss or
canary objectives in production terms: API acceptance is durable, but automatic
dispatch and recovery require a transactional outbox and worker claiming.

## Day 9 API, explicit execution, approval, and workflow operations

- Apply migrations with `uv run alembic upgrade head` before starting the API.
- Supply `X-Team-ID` on all conversation, turn, history, and event requests.
  This header is development identity only and must be replaced by production
  authentication and authorization.
- Supply a unique opaque `Idempotency-Key` for each logical create or continue
  command. Safe retries reuse the exact key and exact request.
- Approval decisions additionally require `X-Actor-ID` and an idempotency key.
  These headers are development fixtures, not authentication or approval-role
  enforcement.
- A `202` response proves durable acceptance, not execution start. Until outbox
  dispatch exists, accepted turns remain `received` unless an explicit runner
  processes them.
- `409 idempotency_conflict` means a key was reused with different content;
  generate a new key only for a genuinely new command.
- `404 resource_not_found` intentionally does not distinguish unknown resources
  from cross-team resources.
- Message history is bounded to 100 items per request and advances with the
  exclusive `after_sequence` cursor.
- Redis is not required for API command correctness or event replay.
- Direct/single-tool execution is invoked through application-level `RunTurn`
  workflow by a trusted development runner/test with team, actor, and granted scopes;
  there is no public execution endpoint or automatic worker claim.
- The bounded workflow emits a direct response, one read-only tool lifecycle, or
  an `approval.required` pause for a mutation. External-side-effect and
  privileged tools remain denied.
- `GET /api/v1/approvals/{id}` exposes only safe summary fields. Decision
  commands approve or reject; rejection and lazy expiry cancel without dispatch.
- Approval consumption and `tool.started` commit before adapter dispatch. A
  crash after that boundary is not automatically recovered or blindly retried.
- Tool events carry stable identifiers and safe codes only. Arguments, results,
  provider exceptions, prompts, and private reasoning are excluded.
- A failed tool commits `tool.failed` before the turn closes with one
  `turn.failed`; completed invocation progress remains durable.
- The Day 9 reference workflow is advanced explicitly through
  `RunWorkflowDiscovery`, `FreezeWorkflowMutationPlan`, and
  `RunApprovedWorkflow`; these are application workflows, not public endpoints
  or automatic worker jobs.
- `workflow.planned`, `workflow.resumed`, and `workflow.terminal` are safe
  progress observations. Only terminal turn events close SSE replay.
- A recreated runner resumes from PostgreSQL and skips committed terminal steps.
  Operators must not reset a persisted running mutation to pending: explicit
  recovery conservatively marks its outcome unknown and stops later dispatch.
- `REVIEW_REQUIRED` means external outcome reconciliation or human review is
  needed. Day 9 provides durable evidence but no reconciliation queue or retry.

## Target structured telemetry

Every event carries correlation identifiers:

- team ID;
- conversation ID;
- turn ID;
- logical invocation ID;
- agent/router/policy/tool/release versions;
- trace and span IDs.

Sensitive values are redacted. Raw payload access is exceptional and audited.

## Target backpressure

The system uses:

- bounded worker concurrency;
- bounded claim batch sizes;
- per-team/agent quotas;
- explicit `429` or accepted-and-queued responses;
- retry-after guidance;
- circuit breakers or temporary tool unavailability after repeated failure.

It must never create unbounded in-memory turn tasks.

## Target tool health

Health combines:

- explicit health checks when available;
- recent success/error/timeout windows;
- manual disable state;
- conformance status.

Unhealthy tools are excluded before semantic routing. The user receives a truthful fallback.

## Incident response workflow

1. Identify affected release, agent, tool, and time window.
2. Pause or roll back candidate traffic when necessary.
3. Inspect correlated structured traces.
4. Determine whether the incident is routing, policy, execution, dependency, data, or model related.
5. Reconcile unknown external outcomes before retries.
6. Add a sanitized regression case.
7. Document root cause and corrective action.
8. Update an ADR if the failure reveals a flawed architectural assumption.

## Runbook scenarios

- worker backlog growing;
- PostgreSQL unavailable;
- Redis unavailable;
- model provider slow or rate limited;
- one tool timing out;
- unknown-outcome queue accumulating;
- elevated policy-denial rate;
- candidate canary regression;
- SSE clients repeatedly reconnecting;
- eval judge instability.

## Rollout operation

```text
DRAFT
→ OFFLINE_EVALUATION
→ SHADOW
→ CANARY
→ FULL
```

Every transition records actor, evidence, thresholds, baseline, and timestamp. Automatic rollback is itself idempotent and returns allocation to the last approved baseline.

## Cost controls

- per-agent model/token budgets;
- maximum routing candidates;
- bounded tool/model retries;
- cache only when correctness and tenancy permit;
- report cost by agent, release, and eval run;
- deterministic/fake model mode for routine development.
