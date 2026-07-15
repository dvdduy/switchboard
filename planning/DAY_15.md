# Day 15 — Safe Replay and Debugging Experience

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Day 14 complete

## Goal

Build an engineer-facing replay/debugging capability that reconstructs a turn
from persisted records, validates derived state, and identifies the responsible
stage without re-executing external side effects.

## Learn

- Debug replay is usually reconstruction, not production rerun.
- Event-derived projections need deterministic rules and schema/version
  awareness.
- Reports must separate observed facts from inferred state.
- Useful debugging classifies routing, context, policy, approval, tool, model,
  persistence, and transport causes.

## Accepted direction

Build a framework-independent `ReplayTurn` query service using pinned versions,
messages/context summaries, turn/attempt/workflow, routing, policies/approvals,
invocations/results, events, and trace stages.

Default replay invokes no models or tools. Produce an ordered timeline,
reconstructed state, consistency warnings, latency breakdown, and safe evidence
references.

Expose a versioned read-only debug API and/or CLI. Any future counterfactual run
must be a separate isolated mode.

## Build checkpoints

### Checkpoint 0 — Incident scenarios

Define questions/evidence for wrong tool, stale context, approval mismatch, tool
failure, duplicate delivery, and stream disconnect.

### Checkpoint 1 — Deterministic replay reducer

Reconstruct state from persisted evidence. Detect gaps, impossible transitions,
missing references, and terminal inconsistencies.

### Checkpoint 2 — Replay report

Create structured timeline entries, source references, derived state, warnings,
durations, and redaction markers.

### Checkpoint 3 — Debug API/CLI

Expose team-scoped read-only access with stable errors/pagination.

### Checkpoint 4 — Golden incidents

Create known incidents and prove the report identifies useful evidence. Missing
or corrupt data must produce warnings rather than invented certainty.

### Checkpoint 5 — Verification and documentation

Update runbook, architecture, security, requirements/use cases, README, and
`PROGRESS.md`.

## Required tests

- deterministic replay;
- gaps/invalid transitions;
- missing-reference warnings;
- team isolation;
- redaction;
- zero external adapter calls;
- terminal and paused workflows;
- restart reconstruction;
- API/CLI contracts.

## Migration impact

Prefer none. Add a projection cache only after measured need; persisted source
records remain authoritative.

## Security and safety considerations

- Debug access is sensitive and scoped.
- Default replay causes no side effects.
- Use safe summaries/references.
- Missing evidence is not proof of absence.
- Audit debug reads when supported.

## Out of scope

- web dashboard;
- counterfactual model runs;
- mutation replay;
- historical editing;
- incident workflow;
- automatic eval-case promotion.

## Definition of done

- [ ] Turn state is reconstructed from durable evidence.
- [ ] Default replay invokes no side effects.
- [ ] Timeline, versions, decisions, outcomes, and latency are inspectable.
- [ ] Inconsistencies become warnings.
- [ ] Access is scoped and redacted.
- [ ] Golden incidents demonstrate diagnosis.
- [ ] Tests and docs pass.

## Suggested commit

`feat(debugging): add safe durable turn replay`

## Earn

You can investigate an incident from one correlated replay report while proving
the debugging path cannot repeat external actions.
