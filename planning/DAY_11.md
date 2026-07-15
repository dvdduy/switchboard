# Day 11 — Tool-Selection Architecture Decision

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Phase 1 complete and tagged `v0.1-platform`

## Goal

Produce a reviewed design document and ADR for Switchboard's tool-selection
boundary before implementing semantic routing. The decision must define eligible
tool filtering, candidate retrieval, final selection, confidence/fallback
semantics, persistence, evaluation, security, and operational behavior.

## Learn

- Ambiguous platform problems should be decomposed before selecting a model or
  library.
- Rules, embeddings, LLM selection, and hybrid routing optimize different
  failure modes.
- Candidate retrieval and final decision are separate stages.
- Confidence is useful only when tied to a documented acceptance/fallback policy
  and measured dataset.
- Authorization, binding, lifecycle, and health filtering must happen before
  semantic ranking.
- Debugging should expose structured evidence and reason codes, not private model
  reasoning.

## Why this matters

This addresses FR-020 through FR-022 and creates evidence of navigating a
complex architectural boundary before writing code.

## Required alternatives

Evaluate at least:

1. deterministic rules only;
2. embedding similarity only;
3. LLM-only selection over all eligible tools;
4. hybrid eligibility filter + embedding retrieval + structured final selector;
5. explicit no-tool/direct-response path.

For each, document correctness, safety, latency, cost, explainability,
cold-start behavior, scaling, testability, vendor coupling, and provider outage
fallback.

## Provisional recommendation to validate

```text
active bound tools
→ ownership/scope/policy eligibility filter
→ availability filter
→ embedding top-k retrieval
→ structured final selector
→ selected / no-match / ambiguous / clarification / unavailable
```

Rules may provide hard exclusions or aliases, but may not bypass authorization,
policy, lifecycle, or health checks. PostgreSQL stores durable routing evidence;
embedding indexes and Redis caches are optimizations.

## Decisions the design must settle

1. `RoutingOutcome` vocabulary.
2. Candidate score versus final confidence.
3. Acceptance threshold and ambiguity margin.
4. Candidate-count and selector-input bounds.
5. Direct-response/no-tool behavior.
6. Reproducibility evidence: router, embedding and selector versions, eligible
   snapshot, candidate scores, result, reason code.
7. Persistence and public events.
8. Stage timeout/failure behavior.
9. Cache keys and invalidation.
10. Day 13 metrics and baseline policy.
11. Security treatment of descriptions and model output.
12. Router-version rollout strategy.

## Build checkpoints

### Checkpoint 0 — Reconcile Phase 1

Audit actual registry, bindings, workflow, policy, context, events, APIs, and
known debt.

### Checkpoint 1 — Problem and decision drivers

Write scenarios, non-goals, scale assumptions, safety constraints, latency
target, reproducibility requirements, and measurable criteria.

### Checkpoint 2 — Alternatives and threat analysis

Compare the required alternatives using obvious, ambiguous, no-match,
unauthorized, unavailable, and mutating scenarios.

### Checkpoint 3 — Recommended architecture

Define ports, domain values, flow, persistence, events, cache boundaries, error
taxonomy, evaluation, and rollout. Include diagrams.

### Checkpoint 4 — Review artifacts

Add `docs/design/TOOL_SELECTION.md`, an ADR, proposed contract changes, the
Days 12–13 acceptance matrix, and explicit deferred questions.

### Checkpoint 5 — Documentation verification

Reconcile architecture, requirements, domain, planning, and `PROGRESS.md`.
No production routing implementation is required today.

## Security and safety considerations

- Tool descriptions, schemas, examples, and model output are untrusted.
- Similarity cannot grant authorization.
- Mutations still pass policy and approval.
- Store scores and reason codes, never hidden reasoning.
- Redact sensitive request/argument content.

## Out of scope

- router implementation;
- provider integration;
- golden-dataset execution;
- live rollout;
- UI.

## Definition of done

- [ ] Phase 1 constraints are documented.
- [ ] Alternatives and trade-offs are compared.
- [ ] Outcomes and fallback rules are precise.
- [ ] Eligibility precedes semantic retrieval.
- [ ] Persistence, evaluation, cache, and rollout boundaries are defined.
- [ ] Threat model and failure matrix exist.
- [ ] ADR/design are reviewed and consistent.
- [ ] `PROGRESS.md` is updated.

## Suggested commit

`docs(routing): define hybrid tool-selection architecture`

## Earn

You can defend the chosen routing architecture, confidence/fallback semantics,
safety boundary, persistence, and evaluation plan before implementation.
