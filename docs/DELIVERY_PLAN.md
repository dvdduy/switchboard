# Delivery Plan

## Delivery philosophy

The course contains 30 sessions. Development should prioritize a thin, reliable end-to-end path, then deepen routing, evaluation, and rollout safety. A visually elaborate frontend is not required.

## Milestone 0 — Documentation and readiness

**Exit criteria**

- project overview, requirements, architecture, use cases, domain model, testing, security, evaluation, and operations reviewed;
- initial ADRs accepted;
- P0/P1 boundaries understood;
- Day 1 scaffold plan approved.

## Milestone 1 — Conversation platform (`v0.1-platform`)

**Target course window:** Days 1–10

Capabilities:

- repository and Docker Compose;
- PostgreSQL migrations and Redis connection;
- versioned agent and conversation model;
- turn state machine and event persistence;
- SSE simulated streaming and reconnect;
- versioned tool registry and conformance basics;
- simple LangGraph-backed orchestration adapter;
- policy/confirmation flow;
- one read-only and one mutating reference tool;
- end-to-end checkpoint.

**Exit demonstration:** Create a conversation, stream a response, route to a registered tool, pause for confirmation, execute once, and inspect durable history.

## Milestone 2 — Reliable routing and execution (`v0.2-routing`)

**Target course window:** Days 11–20

Capabilities:

- tool-selection design document;
- hybrid routing with eligibility filters and structured outcomes;
- routing golden dataset and calibrated confidence reporting;
- structured traces and safe replay;
- latency attribution;
- transactional outbox and worker recovery;
- stable idempotency keys;
- unknown-outcome reconciliation;
- shadow and canary simulation;
- second platform design document.

**Exit demonstration:** Ambiguous requests clarify safely; crash/retry does not duplicate a mutation; candidate router shadows and rolls back after induced regression.

## Milestone 3 — Evaluation and capstone (`v1.0-capstone`)

**Target course window:** Days 21–30

Capabilities:

- immutable eval datasets and reproducible runs;
- deterministic and calibrated judge evaluators;
- CI regression gate with case-level diff;
- minimal eval/trace reporting view;
- simplification ADR;
- onboarding and review artifacts;
- property, contract, and failure-injection tests;
- final CI/CD pipeline;
- architecture diagrams, README, walkthrough, and interview story.

## Core P0 reference tools

1. `search_work_items` — read-only baseline.
2. `create_work_item` — mutation with idempotency and reconciliation.
3. `update_due_date` — scope-sensitive mutation.
4. `slow_project_report` — timeout/rate-limit/failure behavior.
5. `untrusted_document_search` — prompt-injection and redaction tests.

## P1 backlog after capstone

- human-review queue UI;
- team quotas and richer cost governance;
- delegated OAuth credentials;
- explicit conversation migration to newer agent versions;
- larger eval dashboard;
- managed queue adapter;
- object storage for large traces/artifacts;
- multi-model routing;
- advanced online feedback signals.

## Development readiness checklist

- [ ] Confirm repository name and Python version.
- [ ] Confirm packaging/dependency manager.
- [ ] Confirm local model strategy: fake-first plus optional real provider.
- [ ] Confirm PostgreSQL migration library.
- [ ] Confirm sync versus async SQLAlchemy stance.
- [ ] Confirm initial authentication stub and team identity model.
- [ ] Confirm license and public/synthetic-data rules.
- [ ] Create issue/milestone labels matching the three milestones.
- [ ] Create baseline CI workflow.
- [ ] Start `PROGRESS.md` on Day 1.

## Final definition of done

- all P0 requirements have evidence or a documented, accepted deferral;
- clean clone runs locally using documented commands;
- CI is green;
- migrations are reproducible;
- crash, retry, approval, unknown-outcome, and reconnect scenarios pass;
- eval regression and canary rollback are demonstrated;
- architecture and ADRs match the implementation;
- final walkthrough and interview narrative are rehearsed.
