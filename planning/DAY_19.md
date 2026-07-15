# Day 19 — Self-Service Tool Onboarding Design

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Days 5 and 11–18 complete

## Goal

Translate “a new team should register a tool safely without platform-team
involvement” into a durable self-service design covering ownership, versioning,
conformance, credentials, policy, rollout, operations, support, and retirement.

## Learn

- Self-service means a paved road with guardrails, not no governance.
- Authoring, validation, activation, binding, rollout, and execution are
  different control-plane stages.
- Platform leverage comes from contracts, diagnostics, and ownership.
- Onboarding includes deprecation and incident handling.

## Required scenarios

- new read-only tool;
- compatible new version;
- mutating tool;
- conformance failure;
- unavailable credentials/endpoint;
- unhealthy tool;
- deprecation/disable;
- ownership transfer;
- emergency disable;
- historical reproducibility.

## Design topics

1. personas/responsibilities/support;
2. API/CLI and manifest workflow;
3. schemas/effect/scopes/idempotency/reconciliation;
4. secret references;
5. conformance/sandboxing;
6. immutable binding/versioning;
7. policy/approval;
8. routing eligibility/availability;
9. shadow/canary/full rollout;
10. observability/SLO/emergency disable;
11. compatibility/deprecation;
12. audit/tenant/supply-chain;
13. quotas/cost ownership;
14. docs/SDK/test kit;
15. migration from bespoke integrations.

## Build checkpoints

### Checkpoint 0 — Study actual friction

Review evidence from adding reference tools during Phase 1/2.

### Checkpoint 1 — Product journey

Write actors, jobs, success metrics, onboarding flow, support, and non-goals.

### Checkpoint 2 — Architecture/trust

Define control-plane components, credentials, conformance, activation, rollout,
runtime lookup, emergency controls, and audit.

### Checkpoint 3 — Examples

Provide read-only and mutating manifests, diagnostics, lifecycle commands,
conformance report, binding, and rollout examples.

### Checkpoint 4 — Alternatives/decision

Compare centralized, fully self-service, code-owned, remote-manifest, and hybrid
approaches. Recommend an incremental paved road.

### Checkpoint 5 — Gap analysis

Map current support, Phase 2 gaps, and future production gaps. Create follow-up
work without expanding Day 19 into implementation.

### Checkpoint 6 — Review/docs

Add design/ADR updates, architecture/requirements/use cases, and `PROGRESS.md`.

## Required validation

- teams do not edit routing internals;
- tools cannot self-declare away policy;
- secrets are references, not manifest values;
- conformance/rollout gates are explicit;
- emergency disable preserves history;
- ownership/support are assigned;
- deprecation/incidents are covered;
- current versus target is honest.

## Migration impact

None expected.

## Security and safety considerations

Cover malicious manifests, schema bombs, prompt injection in descriptions,
credential isolation, SSRF, supply chain, tenant ownership, effect
misclassification, audit, emergency disable, and privileged approval.

## Out of scope

- implementing every gap;
- production secrets manager;
- marketplace/billing;
- uploaded code;
- certification program;
- polished UI.

## Definition of done

- [ ] Product need and actors are explicit.
- [ ] Full onboarding/lifecycle journey exists.
- [ ] Trust boundaries and gates are defined.
- [ ] Read-only/mutating examples exist.
- [ ] Alternatives and recommendation exist.
- [ ] Current/target gap analysis is honest.
- [ ] Operational ownership is clear.
- [ ] Review feedback and `PROGRESS.md` are incorporated.

## Suggested commit

`docs(tools): design self-service tool onboarding`

## Earn

You can show how an ambiguous request becomes a durable self-service platform
capability with contracts, conformance, rollout, ownership, and safety.
