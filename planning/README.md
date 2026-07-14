# Switchboard Delivery Plans

This folder turns the high-level curriculum in `SWITCHBOARD_COURSE.md` into
bounded, reviewable implementation sessions.

## Planning authority

1. `PROGRESS.md` identifies the active day and completed work.
2. The active `planning/DAY_NN.md` defines the accepted scope for that day.
3. A detailed day plan overrides the simplified curriculum description when the
   two differ.
4. `docs/ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md`, requirements, use cases, ADRs,
   and security/testing documents constrain all plans.
5. Implementation discoveries may refine a future plan, but the plan and
   affected source-of-truth documents must be updated together.

## Delivery rules

- Implement one checkpoint at a time.
- Reconcile repository state before editing.
- Keep domain code independent of FastAPI, SQLAlchemy, Redis, LangGraph, and
  provider SDKs.
- PostgreSQL remains the durable source of truth.
- Redis may optimize latency but must not be required for correctness.
- Durable work must not be launched with untracked in-process background tasks.
- Public events contain stable platform data and never private model reasoning.
- Mutating tools remain inert until policy and approval requirements are met.
- Completed external side effects are never repeated merely because execution
  resumes.
- Do not claim exactly-once behavior across arbitrary external systems.
- A day is complete only after code, tests, migrations, documentation, and
  `PROGRESS.md` agree.

## Planning maturity

| Days | State | Planning maturity |
|---:|---|---|
| 1–3 | Complete | Implemented and reconciled with actual delivery |
| 4–6 | Planned | Detailed checkpoint and acceptance-level plans |
| 7–9 | Planned | Detailed capability plans with provisional low-level design |
| 10 | Planned | Phase integration, hardening, evidence, and release checkpoint |
| 11–20 | Roadmap | Create capability-level plans after Phase 1 is stable |
| 21–30 | Roadmap | Keep directional until the evaluation architecture is closer |

## Navigation

### Phase 1 — Conversation Platform Foundations

- [Day 1 — Repository Scaffold and Architecture Skeleton](DAY_01.md)
- [Day 2 — Conversation and Execution Data Model](DAY_02.md)
- [Day 3 — Durable Execution Events and Reconnectable SSE](DAY_03.md)
- [Day 4 — Token-Budgeted Context Window Management](DAY_04.md)
- [Day 5 — Versioned Tool Registry and Conformance](DAY_05.md)
- [Day 6 — Shared Versioned Conversation API](DAY_06.md)
- [Day 7 — Framework-Isolated LangGraph Agent Loop](DAY_07.md)
- [Day 8 — Policy Guardrails and Durable Approval](DAY_08.md)
- [Day 9 — Durable Multi-Tool Pause and Resume](DAY_09.md)
- [Day 10 — Phase 1 Integration and v0.1 Platform Checkpoint](DAY_10.md)

## Codex usage

Before asking Codex to implement a checkpoint, have it read:

1. `AGENTS.md`;
2. `PROGRESS.md`;
3. the active day plan;
4. architecture and domain documents;
5. relevant requirements, use cases, ADRs, and the current diff.

Codex should stop after one checkpoint, report commands actually executed, and
never commit or advance the next checkpoint without explicit approval.
