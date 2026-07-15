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

**Status:** Complete. Day 9 is present at `7541c30`, the worktree began clean,
Ruff, strict mypy, and all 458 tests passed, and `PROGRESS.md` agrees that Day 9
is complete. Docker was unavailable from PowerShell during the initial audit;
the later checkpoint-5 smoke passed through WSL Docker. Day 10 is frozen as
integration and release hardening: no public
execution endpoint, outbox/worker leasing, new aggregate, or changed `202`/SSE/
approval contract.

### Checkpoint 1 — Deterministic demo environment

Add/refine seed/reset commands for team, agent, context policy, active bound
tools, reference data, fake model, read-only search, idempotent due-date update,
and clear startup validation. No credentials.

**Status:** Complete. `switchboard-demo-environment validate|reset|seed` checks
the Alembic head, guards destructive reset to explicit local/test database
targets, and creates stable team/actor/agent identities plus the fixed context
policy. Seed runs the existing manifest validation and full conformance suites,
activates both reference tool versions, and binds their exact versions to the
final immutable agent version. Complete seed replay is a no-op; partial or
changed state requires explicit reset. The CLI reports the scripted model mode
and stable synthetic work-item IDs without introducing provider credentials or
a public execution path.

### Checkpoint 2 — Read-only journey

Script/test create conversation, explicit run, bounded context, read-only tool,
committed events, disconnect/reconnect with `Last-Event-ID`, reconstructed output,
and ordered history. Collect safe IDs/stage timing.

**Status:** Complete. `switchboard-demo read-only` starts from the deterministic
seed, creates the turn through the public `/api/v1` contract, and invokes only
the trusted application-level `RunTurn` execution boundary. The scripted model
selects the exact active search-tool version, bounded context accounting is
captured, and durable invocation/result evidence commits before the assistant
message. The client stops consuming SSE after the first response delta,
reconnects with the exclusive cursor, proves one contiguous event sequence,
reconstructs the exact response, and verifies ordered public history plus the
pinned agent version. Output includes safe IDs and one-sample local stage
timings without production performance claims. The journey adds no public
execution endpoint and requires guarded reset/seed before replay.

### Checkpoint 3 — Approval and multi-tool journey

Continue conversation, persist plan, execute discovery once, pause, dispose
runner, approve through `/api/v1`, resume with new runner, execute approved
mutations once, stream final progress, verify history/audit, and prove duplicate
resume causes no duplicate mutation.

**Status:** Complete. `switchboard-demo approval-workflow` continues the
read-only conversation through `/api/v1`, explicitly claims the accepted turn
for the trusted runner, and uses the delivered Day 9 discovery/freeze/resume
workflows without adding an HTTP execution endpoint. Discovery replay preserves
one adapter call; the frozen two-mutation plan is exposed through a value-free
public approval and approved through `/api/v1`. Resume uses a distinct UoW and
new runner composition, commits two idempotent mutations and the final message,
and a separately constructed duplicate runner replays terminal evidence with
zero adapter calls. The journey verifies consumed approval, per-invocation
policy evidence, stable idempotency keys, safe final SSE progress, and ordered
four-message history. Local stage timings remain one-sample demo evidence.

### Checkpoint 4 — Failure and recovery demonstration

Exercise client disconnect after acceptance, SSE reconnect, runner failure after
partial output, malformed model action, disabled tool, approval expiry/argument
change, duplicate command/resume, transaction rollback, and unknown mutation
outcome stopping without blind retry.

Document automatic versus explicit/manual recovery.

Implemented as the `switchboard-demo-failures` development validation harness.
Its stable catalog maps all nine requested failure classes to focused executable
pytest evidence, durable outcomes, and `automatic`, `explicit`, or `manual`
recovery ownership. `docs/OPERATIONS.md` records the operator rules, including
that an unknown external mutation outcome requires reconciliation by idempotency
key and must never be blindly retried. This checkpoint adds no migration, public
execution endpoint, retry loop, reconciliation queue, or production fault
injection.

### Checkpoint 5 — Contract, performance, and operability

Verify OpenAPI/errors, migration base-to-head and downgrade/re-upgrade, clean
Compose startup, health/readiness, reset/seed/demo commands, redaction, context
budgets, bounded graph/workflow, and structured stage timing.

Measure local targets such as acceptance latency, time to first committed event,
replay correctness, and duplicate mutation count. Report environment/sample
size; do not call this production capacity.

**Status:** Complete. The
`switchboard-demo-verify` catalog runs 23 focused checks covering public
OpenAPI/errors (including approvals), base-to-head downgrade/re-upgrade,
health/readiness, guarded demo controls, redaction, context budgets, bounded
LangGraph/workflow execution, and the Compose startup contract. The runtime
image now contains Alembic assets and a one-shot `migrate` service must finish
before API or worker startup. An optional isolated Compose smoke builds the
stack, probes live/ready, and removes its dedicated volumes. The clean-volume
smoke passed through the workstation's WSL Docker daemon: migration completed,
API/worker startup succeeded, and live/ready reported PostgreSQL and Redis
available. Both journeys now label environment and sample size, disclaim
production capacity, and retain structured
stage timings; the read-only journey adds observed first-committed-event latency,
while the approval journey reports two logical mutations and zero calls on
duplicate resume. No schema migration, public contract change, production load
claim, tracing system, or worker dispatch behavior was added.

### Checkpoint 6 — Documentation and interview evidence

Update README, architecture status, domain, requirements evidence, use-case
status, security/testing/operations, `PROGRESS.md`, completion summaries, debt,
and Phase 2 handoff.

Prepare a 60-second walkthrough, 5-minute demo, design deep dive, failure story,
safety story, and quantified evidence table.

**Status:** Complete. Phase 1 status is reconciled across the course, README,
architecture, domain, requirements, use cases, security, testing, operations,
and `PROGRESS.md`. `docs/PHASE_1_EVIDENCE.md` records the honest capability
boundary, one local measured sample, 60-second walkthrough, five-minute demo,
design deep dive, failure and safety stories, and ordered Phase 2 handoff.
`PROGRESS.md` marks integration implemented while keeping checkpoint 7 release
verification, review, commit, and tag explicitly pending.

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

- [x] Clean local environment runs documented Phase 1 demo.
- [x] Clients use documented `/api/v1`.
- [x] Read-only and approval-gated mutation journeys work.
- [x] Context is observable and within budget.
- [x] SSE reconnect reconstructs output.
- [x] Process recreation resumes progress.
- [x] Duplicate command/resume causes no duplicate logical mutation.
- [x] Failures produce documented safe outcomes.
- [x] Static, migration, test, E2E, and container gates pass.
- [x] Documentation has no capability inflation.
- [x] `PROGRESS.md` marks Phase 1 implementation complete with release pending.
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
