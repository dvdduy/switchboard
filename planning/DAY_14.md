# Day 14 — Structured Turn Tracing

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Day 13 complete

## Goal

Create correlated structured per-turn tracing that explains stages, versions,
decisions, tool calls, outcomes, and latency without storing private model
reasoning or depending on one telemetry vendor.

## Learn

- Events, audit records, metrics, logs, and traces are different products.
- Correlation and stage boundaries make latency attributable.
- Observability requires redaction and cardinality control.
- OpenTelemetry is an adapter/export format, not the durable business model.
- “Reasoning steps” means structured platform decisions, not chain-of-thought.

## Accepted direction

Define stages for command acceptance, queue, context, eligibility, retrieval,
selection, policy/approval, tool dispatch/result, model generation, persistence,
and stream delivery.

Correlate team, conversation, turn, attempt, workflow, invocation, routing, and
release IDs where applicable.

Store durable structured evidence in PostgreSQL/events. Export operational spans
through a `TelemetrySink` port. Store versions, reason codes, outcomes,
durations, tokens/cost, and redacted summaries. Bound attribute size and
cardinality.

## Build checkpoints

### Checkpoint 0 — Contract and redaction review

Inventory current events/logs/timings and define stage names, correlation rules,
attribute allowlist, and redaction.

### Checkpoint 1 — Trace values and ports

Build span/stage records, timing helpers, outcome/error taxonomy, and telemetry
port. Test nesting, timing, redaction, and bounds.

### Checkpoint 2 — Instrument execution

Add instrumentation across command, routing, policy, tool, model, persistence,
and streaming. Failures close spans with classified outcomes.

### Checkpoint 3 — Durable trace projection

Build a queryable per-turn projection from durable evidence, or add minimal
trace persistence only where derivation is insufficient.

### Checkpoint 4 — Export adapter

Add structured logging and optional OpenTelemetry export. Exporter failure must
not fail execution.

### Checkpoint 5 — Verification and documentation

Show one multi-tool trace with durations/outcomes and update architecture,
operations, security, testing, events, and `PROGRESS.md`.

## Required tests

- correlation;
- stage order/nesting;
- redaction/size limits;
- failure spans;
- exporter outage isolation;
- restart durability;
- multi-tool/approval trace;
- no secrets/private reasoning;
- architecture boundaries.

## Migration impact

Possible minimal trace/projection records or event extensions. Prefer deriving
from existing durable records where correct and queryable.

## Security and safety considerations

- Attribute allowlist.
- Hash/redact sensitive arguments.
- Team-scoped trace reads.
- No user text as metric labels.
- No chain-of-thought.
- Audit debug access when identity exists.

## Out of scope

- full UI;
- production collector deployment;
- telemetry vendor selection;
- adaptive sampling;
- retention policy;
- replay execution.

## Definition of done

- [ ] One turn has correlated structured stage evidence.
- [ ] Versions, decisions, tools, outcomes, and latency are visible.
- [ ] Redaction/cardinality rules are enforced.
- [ ] Export is replaceable and noncritical.
- [ ] Durable trace survives restart.
- [ ] No private reasoning is persisted or exposed.
- [ ] Tests and docs pass.

## Suggested commit

`feat(observability): add structured correlated turn tracing`

## Earn

You can diagnose a turn using safe structured evidence and explain the
boundaries among durable events, audit, metrics, logs, and trace exports.
