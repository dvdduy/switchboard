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

## Structured telemetry

Every event carries correlation identifiers:

- team ID;
- conversation ID;
- turn ID;
- logical invocation ID;
- agent/router/policy/tool/release versions;
- trace and span IDs.

Sensitive values are redacted. Raw payload access is exceptional and audited.

## Backpressure

The system uses:

- bounded worker concurrency;
- bounded claim batch sizes;
- per-team/agent quotas;
- explicit `429` or accepted-and-queued responses;
- retry-after guidance;
- circuit breakers or temporary tool unavailability after repeated failure.

It must never create unbounded in-memory turn tasks.

## Tool health

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
