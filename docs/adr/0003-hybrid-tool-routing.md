# ADR-0003: Hybrid Tool Routing with Explicit Fallback

- **Status:** Proposed; validate on Day 11
- **Date:** 2026-07-12

## Context

Rules are predictable but difficult to maintain as the tool registry grows. Embedding similarity is efficient for candidate retrieval but may confuse semantically close tools. An LLM can reason over schemas and descriptions but is slower, more expensive, and nondeterministic.

## Decision

Use a hybrid pipeline:

1. filter tools by agent binding, authorization, lifecycle, and health;
2. retrieve a small candidate set using embeddings;
3. make a structured final selection over candidates;
4. return explicit selected, no-match, ambiguous, clarification, unavailable, or unauthorized outcomes;
5. measure confidence calibration and false execution, not only raw accuracy.

## Alternatives considered

- pure rules;
- embedding top-1 selection;
- LLM over the entire registry;
- one dedicated classifier model.

These remain benchmark candidates in the Day 11 design document.

## Consequences

- More components than a single prompt, but better cost and debuggability.
- Requires versioned embeddings/router configuration and a routing dataset.
- Confidence must be empirically calibrated rather than treated as an intrinsic probability.
