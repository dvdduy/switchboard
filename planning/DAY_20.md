# Day 20 — Phase 2 Integration and v0.2 Routing Checkpoint

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Days 11–19 complete

## Goal

Integrate Phase 2 into one reproducible demonstration: hybrid routing with safe
fallback, measured routing quality, structured trace and replay, attributed
latency, availability, shadow/canary rollback, and crash-safe durable execution.

Tag the verified result as `v0.2-routing`.

## Expected capability inventory

- all Phase 1 capabilities;
- tool-selection design/ADR;
- hybrid router;
- routing dataset/baseline;
- safe tracing and replay;
- latency attribution and availability;
- staged rollout/rollback;
- outbox, worker leases, recovery, and reconciliation;
- self-service onboarding design.

## Accepted direction

Demonstrate:

- selected read-only tool;
- ambiguous/no-match fallback;
- approval-gated mutation;
- SSE reconnect;
- safe trace replay;
- routing baseline and deliberate regression;
- candidate rollout and auto-rollback;
- worker crash/recovery without duplicate mutation.

Use synthetic data and deterministic provider-free adapters. Capture local
quality/latency evidence with environment/sample context. Freeze Phase 2 scope
and document remaining debt honestly.

## Build checkpoints

### Checkpoint 0 — Phase audit

Audit Days 11–19 definitions, schema, tests, docs, and behavior. Fix blockers.

### Checkpoint 1 — Routing journey

Show filtering, candidates, outcome, policy/approval, execution, events, and
history.

### Checkpoint 2 — Quality/debug journey

Run the dataset, show baseline and regression, then inspect safe replay with
trace/latency.

### Checkpoint 3 — Rollout journey

Run shadow, canary, induced regression, auto-rollback, and baseline restoration.

### Checkpoint 4 — Recovery journey

Inject crashes, reclaim work, preserve invocation identity, reconcile lost
response, and prove no duplicate effect.

### Checkpoint 5 — Operability/performance

Verify Compose, seed/reset/demo, shutdown, queue visibility, health, cache loss,
migrations, redaction, and local metrics.

### Checkpoint 6 — Documentation/evidence

Update README, architecture, domain, requirements, use cases, security,
evaluation/testing/operations, `PROGRESS.md`, and Phase 3 handoff. Prepare
walkthrough, demo, trade-off, metrics, rollout, and reliability stories.

### Checkpoint 7 — Release/tag

Run full static, migration, unit, integration, API, worker, failure, routing
eval, E2E, and container gates from clean state. Review claims/secrets/artifacts.
Create reviewed commit and annotated `v0.2-routing` tag only after approval.

## Required evidence

- routing top-1/top-k, coverage, fallback, false execution;
- ambiguous/no-match safety;
- one structured trace/replay;
- stage p50/p95;
- optimization before/after;
- rollback timing;
- crash matrix;
- no-duplicate mutation counter;
- clean local demo.

## Migration impact

No new feature migration expected. Corrective follow-up migration only; never
rewrite shared migrations.

## Security and safety considerations

- no secrets/sensitive raw values;
- no private reasoning;
- shadow has no side effects;
- canary preserves policy;
- unknown outcomes never blind-retry;
- replay is read-only/scoped;
- local identity is nonproduction.

## Out of scope

- general Phase 3 eval platform;
- LLM judge;
- CI eval gate;
- eval dashboard;
- production SSO/RBAC;
- production scale;
- managed broker/multiregion;
- real production rollout;
- UI.

## Definition of done

- [ ] Clean local Phase 2 demo runs.
- [ ] Routing quality/fallback are measured.
- [ ] Trace/replay diagnose safely.
- [ ] Latency/availability evidence is reproducible.
- [ ] Canary regression auto-rolls back.
- [ ] Worker crash recovers without duplicate mutation.
- [ ] Full static, migration, test, eval, failure, E2E, container gates pass.
- [ ] Documentation has no capability inflation.
- [ ] `PROGRESS.md` marks Phase 2 complete.
- [ ] Reviewed commit/tag exist.

## Suggested commit and tag

Commit:

`feat(routing): complete Phase 2 reliable routing platform`

Tag:

`v0.2-routing`

## Earn

You can demonstrate a measured, observable, safely rolled-out routing platform
and explain durable dispatch, at-least-once delivery, idempotency, and
reconciliation under failure.

## Phase 3 handoff

Refine Days 21–30 using actual Phase 2 routing data, traces, versions, and
release model. Generalize evaluation without duplicating the routing harness.
