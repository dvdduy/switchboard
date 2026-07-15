# Day 17 — Staged Rollout and Automatic Rollback

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Days 13–16 complete

## Goal

Create a local rollout system for a new router or tool integration that moves
through shadow and canary stages, compares bounded signals with a pinned
baseline, and automatically rolls back an induced regression.

## Learn

- A release is immutable candidate configuration; deployment is mutable stage
  state.
- Shadow must never produce side effects.
- Canary decisions need a baseline, bounded window, minimum sample, and explicit
  thresholds.
- Rollback must be idempotent.
- Offline evaluation and live signals complement each other.

## Accepted direction

Model immutable `Release` and mutable `Deployment` with stages:

`DRAFT → OFFLINE_EVALUATION → SHADOW → CANARY → FULL`, plus `ROLLED_BACK`.

Pin agent/router/tool/policy configuration. Shadow records candidate decisions
but never dispatches candidate tools. Canary uses deterministic stable-key
allocation.

Compare candidate to baseline using routing/fallback differences, error and
completion rates, latency, policy violations, and estimated cost. Require a
minimum sample and bounded window. Persist transitions and rollback evidence.

Use a deterministic simulator and do not claim production traffic.

## Build checkpoints

### Checkpoint 0 — Rollout policy

Define stages, prerequisites, metrics, thresholds, sample/window rules, critical
signals, and the induced regression.

### Checkpoint 1 — Domain

Build release/deployment lifecycle, pinned configuration, baseline reference,
traffic allocation, rollback reason, and audit events.

### Checkpoint 2 — Persistence/control plane

Add migration, repositories, compare-and-set transitions, create/promote/pause/
rollback commands, and idempotent rollback.

### Checkpoint 3 — Shadow

Implement duplicate routing/evaluation that records candidate behavior but
cannot execute candidate side effects.

### Checkpoint 4 — Canary and monitor

Implement allocation, metric-window aggregation, baseline comparison, and
automatic rollback.

### Checkpoint 5 — Regression demonstration

Deploy a degraded candidate, gather sufficient signals, trigger rollback, and
prove baseline restoration.

### Checkpoint 6 — Verification/documentation

Update architecture, domain, requirements/use cases, operations, security,
README, measured evidence, and `PROGRESS.md`.

## Required tests

- lifecycle transitions;
- immutable release;
- shadow side-effect prevention;
- deterministic allocation;
- sample/window rules;
- critical threshold;
- rollback idempotency/races;
- baseline restoration;
- restart persistence;
- induced-regression E2E.

## Migration impact

Expected release, deployment, metric-window/decision, and audit persistence.

## Security and safety considerations

- Shadow cannot execute mutations.
- Canary preserves policy/approval.
- Rollback authority is control-plane scoped.
- Versions are immutable and auditable.
- Missing or manipulated metrics produce conservative behavior.

## Out of scope

- Kubernetes controllers;
- production traffic;
- multiregion rollout;
- full Phase 3 eval gate;
- UI console.

## Definition of done

- [ ] Durable guarded rollout stages exist.
- [ ] Shadow causes no side effects.
- [ ] Canary allocation is deterministic.
- [ ] Candidate compares with a pinned baseline.
- [ ] Induced regression auto-rolls back.
- [ ] Rollback restores baseline idempotently.
- [ ] Tests, simulator, and docs pass.

## Suggested commit

`feat(rollout): add shadow canary and automatic rollback`

## Earn

You can demonstrate a candidate moving through shadow/canary and rolling back on
measured regression with safety and auditability.
