# Day 13 — Routing Evaluation Harness

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Day 12 complete

## Goal

Build a reproducible routing-evaluation harness using a versioned golden dataset
of queries and expected outcomes, then measure selection quality, fallback
behavior, and safety-critical false execution.

## Learn

- Accuracy alone hides unsafe behavior.
- Evaluation needs ambiguity, no-match, unavailable, and unauthorized cases.
- Coverage trades off against false execution.
- Dataset, router, registry snapshot, thresholds, and seed must be pinned.
- Case-level diagnostics matter more than one aggregate score.

## Accepted direction

Create a routing-specific harness without prematurely building the complete
Phase 3 evaluation platform.

Each case includes:

- stable case ID and category;
- request and minimal context;
- team/agent/tool-registry fixture;
- expected outcome;
- acceptable tool versions or no-tool result;
- safety criticality and tags.

Each run pins dataset, router, embeddings, selector, eligible-tool snapshot,
seed, thresholds, and code revision when available.

Metrics include:

- top-1 accuracy;
- top-k retrieval recall;
- accepted coverage;
- fallback/no-match/ambiguity rates;
- false-selection rate;
- safety-critical false-execution rate;
- per-tool/category results;
- latency distribution.

## Build checkpoints

### Checkpoint 0 — Dataset design

Cover obvious matches, close competitors, ambiguity, no match, unauthorized or
unavailable preferred tools, mutation approval, malicious descriptions, and
paraphrases.

### Checkpoint 1 — Versioned dataset contract

Create immutable validated YAML/JSON or equivalent with deterministic content
identity and stable case IDs.

### Checkpoint 2 — Runner and metrics

Run the actual router for every case and record outcome, candidates, confidence,
latency, and reason. Implement deterministic metric calculations.

### Checkpoint 3 — Reproducible report

Persist or serialize run metadata and case results. Produce a report showing
failures and changed cases.

### Checkpoint 4 — Baseline and threshold policy

Create an approved baseline. Use stricter thresholds for safety-critical false
execution. Demonstrate a deliberately degraded router.

### Checkpoint 5 — Verification and documentation

Run the harness, record honest numbers, and update routing design, testing,
requirements, README, and `PROGRESS.md`.

## Required tests

- dataset validation/content identity;
- metric math;
- deterministic ordering;
- outcome expectations;
- safety classification;
- malformed diagnostics;
- repeated-run reproducibility;
- baseline comparison;
- provider-free CI execution.

## Migration impact

Optional. Repository-controlled artifacts may be sufficient. If PostgreSQL is
used, keep the schema narrow and avoid duplicating the future general eval model.

## Security and safety considerations

- Use synthetic/sanitized requests.
- Treat dataset text as untrusted.
- Store no secrets or hidden reasoning.
- Verify routing never bypasses policy/approval.
- Report false execution separately from harmless fallback.

## Out of scope

- LLM-as-judge;
- response-quality scoring;
- CI gate;
- dashboard;
- human-review queue;
- online evaluation.

## Definition of done

- [ ] Dataset is versioned and validated.
- [ ] Cases cover positive, fallback, unavailable, and safety paths.
- [ ] Runs pin router and registry inputs.
- [ ] Metrics include coverage, fallback, and false execution.
- [ ] Case-level diagnostics identify failures.
- [ ] Baseline comparison detects a deliberate regression.
- [ ] Deterministic local/CI mode passes.
- [ ] Documentation records measured evidence honestly.

## Suggested commit

`feat(routing-eval): add reproducible routing quality harness`

## Earn

You can discuss routing quality using measured recall, coverage, fallback, and
safety-critical false execution rather than anecdotes.
