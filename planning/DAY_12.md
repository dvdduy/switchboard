# Day 12 — Hybrid Semantic Tool Routing

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Day 11 design and ADR accepted

## Goal

Implement deterministic eligibility filtering, semantic candidate retrieval,
structured final selection, explicit confidence/fallback outcomes, and durable
routing evidence pinned to exact versions.

## Accepted direction

Follow Day 11. The expected stages are:

1. load the pinned agent and bound tool versions;
2. exclude cross-team, inactive, unauthorized, or unavailable tools;
3. normalize the routing query from bounded context/current request;
4. retrieve top-k candidates through an `EmbeddingRetriever` port;
5. call a structured `ToolSelector` port;
6. validate output against the candidate set;
7. produce one explicit `RoutingOutcome`;
8. persist safe version-pinned evidence;
9. emit stable routing events;
10. route selected mutations through policy/approval.

Use deterministic fake embedding and selector adapters for CI.

## Build checkpoints

### Checkpoint 0 — Reconcile the accepted design

Map each design contract to current Phase 1 code and identify prerequisite
refactoring.

### Checkpoint 1 — Routing domain and ports

Build routing IDs/outcomes, eligible/candidate value objects, immutable
`RoutingDecision`, ports, reason codes, and threshold/ambiguity tests.

### Checkpoint 2 — Eligibility pipeline

Filter by binding, team, lifecycle/conformance, scopes, availability, and
preselection policy. Preserve exclusion reason codes.

### Checkpoint 3 — Retrieval and structured selection

Implement deterministic embeddings, optional provider adapter, top-k retrieval,
structured selection, bounds, timeout, and malformed-output fallback. Reject
noncandidate selections.

### Checkpoint 4 — Persistence and orchestration

Persist router/config versions, eligible snapshot, candidates/scores, outcome,
confidence, reason, and stage durations. Integrate with the agent loop while
preserving policy/approval.

### Checkpoint 5 — Events and integration tests

Prove selected, no-match, ambiguous, clarification, unavailable, unauthorized,
and mutating cases.

### Checkpoint 6 — Verification and documentation

Run all gates and update architecture, domain, events, requirements, README,
`PROGRESS.md`, and the Day 11 design.

## Required tests

- eligibility and exclusion;
- threshold/ambiguity boundaries;
- malformed selector/timeout;
- candidate membership;
- persistence/reopen;
- disabled-before-dispatch;
- cross-team/scope rejection;
- mutation approval;
- deterministic local router;
- migration/architecture checks.

## Migration impact

Expected routing decisions and candidate evidence, with router/configuration
version references and indexes for turn/eval lookup.

## Security and safety considerations

- Similarity never overrides authorization or policy.
- Treat descriptions and selector output as untrusted.
- Store no private reasoning.
- Redact sensitive request content.
- Bound candidates, prompt size, latency, and cost.
- Provider outage yields truthful fallback.

## Out of scope

- calibrated production confidence;
- dashboard;
- live rollout;
- learned reranker training;
- large vector database;
- automatic health computation.

## Definition of done

- [ ] Eligibility precedes semantic retrieval.
- [ ] Retrieval/selection are replaceable ports.
- [ ] Every request has an explicit outcome.
- [ ] Invalid selections are rejected.
- [ ] Evidence is durable and version-pinned.
- [ ] Mutations still require policy/approval.
- [ ] Deterministic CI mode exists.
- [ ] Migrations, tests, static checks, containers, and docs pass.

## Suggested commit

`feat(routing): add hybrid semantic tool selection`

## Earn

You can demonstrate hybrid routing with safe fallback and explain eligibility,
structured selection, reproducibility, and confidence limits.
