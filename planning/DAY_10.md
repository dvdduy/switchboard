# Day 10 — Phase 1 Integration and v0.1 Platform Checkpoint

**Status:** Planned
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Days 1–9 complete

## Goal

Integrate and harden Phase 1 into one reproducible local demonstration: create
and continue a versioned conversation, assemble bounded context, execute a
direct/read-only response, pause a mutation for approval, resume a durable
multi-tool workflow after process recreation, stream committed events with
reconnect, and preserve ordered history.

Tag the verified result as `v0.1-platform`.

## Learn

This is a consolidation day focused on integration boundaries, contract
consistency, failure evidence, operational reproducibility, documentation
honesty, measurement, and communication. No major subsystem is introduced.

## Why this matters

Phase 1 must be a coherent capability another team can build against, not a
collection of entities and isolated tests.

## Expected capability inventory

- modular monolith with separate API/worker entry points;
- PostgreSQL durable truth and Redis as optional infrastructure;
- versioned agents/conversations;
- ordered messages, turns, attempts, events;
- reconnectable SSE;
- bounded context and summary provenance;
- versioned tool registry/conformance;
- `/api/v1` conversation commands/history;
- LangGraph behind an orchestration port;
- durable tool invocations;
- policy decisions/fingerprint-bound approvals;
- durable sequential multi-tool workflow and restart-safe resume.

Any missing prerequisite is an integration blocker, not demo-only state.

## Accepted direction

1. Add integration glue, fixtures, diagnostics, docs, and hardening—not new
   architecture.
2. Use deterministic fake model/reference tools; no paid credentials.
3. Provide one scripted journey with stable seeded identities.
4. Exercise read-only and mutating flows.
5. Prove mid-stream SSE reconnect.
6. Prove process recreation around approval pause.
7. Prove duplicate resume keeps logical mutation count at one.
8. Record debt: no outbox/automatic worker claiming, real provider, semantic
   router, unknown-outcome reconciliation, production auth, or Redis
   notification correctness.
9. Capture measurable evidence without production-scale claims.
10. Tag only after all gates/docs are green.

## Build checkpoints

### Checkpoint 0 — Phase audit and scope freeze

Audit Days 1–9 against progress/plans, architecture/domain/requirements/use
cases, migrations/schema, API/OpenAPI, tests, and known debt. Produce blockers,
fix prerequisites, and reject unrelated scope.

### Checkpoint 1 — Deterministic demo environment

Add/refine seed/reset commands for team, agent, context policy, active bound
tools, reference data, fake model, read-only search, idempotent due-date update,
and clear startup validation. No credentials.

### Checkpoint 2 — Read-only journey

Script/test create conversation, explicit run, bounded context, read-only tool,
committed events, disconnect/reconnect with `Last-Event-ID`, reconstructed output,
and ordered history. Collect safe IDs/stage timing.

### Checkpoint 3 — Approval and multi-tool journey

Continue conversation, persist plan, execute discovery once, pause, dispose
runner, approve through `/api/v1`, resume with new runner, execute approved
mutations once, stream final progress, verify history/audit, and prove duplicate
resume causes no duplicate mutation.

### Checkpoint 4 — Failure and recovery demonstration

Exercise client disconnect after acceptance, SSE reconnect, runner failure after
partial output, malformed model action, disabled tool, approval expiry/argument
change, duplicate command/resume, transaction rollback, and unknown mutation
outcome stopping without blind retry.

Document automatic versus explicit/manual recovery.

### Checkpoint 5 — Contract, performance, and operability

Verify OpenAPI/errors, migration base-to-head and downgrade/re-upgrade, clean
Compose startup, health/readiness, reset/seed/demo commands, redaction, context
budgets, bounded graph/workflow, and structured stage timing.

Measure local targets such as acceptance latency, time to first committed event,
replay correctness, and duplicate mutation count. Report environment/sample
size; do not call this production capacity.

### Checkpoint 6 — Documentation and interview evidence

Update README, architecture status, domain, requirements evidence, use-case
status, security/testing/operations, `PROGRESS.md`, completion summaries, debt,
and Phase 2 handoff.

Prepare a 60-second walkthrough, 5-minute demo, design deep dive, failure story,
safety story, and quantified evidence table.

### Checkpoint 7 — Release verification and tag

Run the complete gate, expected to include:

```powershell
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest
uv run pytest tests/integration -q
uv run alembic downgrade base
uv run alembic upgrade head
docker compose build
```

Run deterministic E2E from reset state. Review git status/diff, migrations,
OpenAPI, claims, secrets/artifacts. Create final commit and annotated
`v0.1-platform` tag only after approval.

## Required tests and evidence

### Automated

All unit/application/adapter/persistence/API/architecture tests, migration
base-to-head/downgrade, context properties, conformance, API idempotency,
SSE reconnect, approval races, workflow restart/duplicate resume, deterministic
E2E, and container smoke.

### Manual/review

OpenAPI usability, clean-clone setup, no paid credentials, redaction, demo
clarity, architecture/debt honesty, and walkthrough rehearsal.

## Migration impact

Day 10 should not add feature migration. A follow-up migration may fix a Phase 1
correctness issue; never rewrite shared migrations.

## Security and safety considerations

No secrets/raw sensitive arguments in fixtures/logs/events/docs. Development
identity is labelled. Mutations remain fingerprint-bound/approval-gated. Tool/
model output is untrusted. Unknown outcomes stop safely. Reset tooling is
development-only and guarded.

## Out of scope

Phase 2 routing/confidence, production auth, outbox/worker leasing unless moved
by ADR/scope trade-off, real provider, unknown reconciliation, live health
disable, eval/rollout, production load certification, and UI.

## Definition of done

- [ ] Clean local environment runs documented Phase 1 demo.
- [ ] Clients use documented `/api/v1`.
- [ ] Read-only and approval-gated mutation journeys work.
- [ ] Context is observable and within budget.
- [ ] SSE reconnect reconstructs output.
- [ ] Process recreation resumes progress.
- [ ] Duplicate command/resume causes no duplicate logical mutation.
- [ ] Failures produce documented safe outcomes.
- [ ] Static, migration, test, E2E, and container gates pass.
- [ ] Documentation has no capability inflation.
- [ ] `PROGRESS.md` marks Phase 1 complete.
- [ ] Reviewed commit and `v0.1-platform` tag exist.

## Suggested commit and tag

Commit: `feat(platform): complete Phase 1 conversation platform`
Tag: `v0.1-platform`

## Earn

You can demonstrate and explain a framework-independent conversation platform
with durable state, bounded context, versioned/conformant tools, reconnectable
streaming, approval-gated mutations, and restart-safe multi-step execution.
PostgreSQL owns correctness; LangGraph and Redis are adapters/optimizations.

## Phase 2 handoff

Before Day 11, refine Days 11–20 against the actual Phase 1 model: routing design,
semantic retrieval, routing eval, tracing, latency attribution, durable dispatch/
worker recovery, unknown-outcome reconciliation, and staged rollout.
