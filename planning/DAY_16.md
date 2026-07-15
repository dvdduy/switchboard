# Day 16 — Latency Attribution and Tool Availability

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Days 14–15 complete

## Goal

Measure latency by execution stage, define truthful tool availability, identify
the dominant bottleneck, and implement one safe targeted optimization with
reproducible before/after evidence.

## Learn

- End-to-end latency hides queue, routing, model, tool, persistence, and stream
  costs.
- Percentiles and representative workloads matter.
- Availability is a routing input with staleness.
- Caches require versioned keys and correctness fallbacks.
- Optimization begins with attribution.

## Accepted direction

Derive durations from traces. Benchmark direct, read-only, approval, and
multi-tool flows using p50/p95 and sample counts.

Introduce availability:
`AVAILABLE`, `DEGRADED`, `UNAVAILABLE`, `UNKNOWN`.

Routing excludes unavailable tools and follows documented policy for unknown or
degraded states.

Choose one optimization after measurement, such as immutable registry caching,
embedding caching, bounded persistence batching, or notification-assisted event
polling. PostgreSQL remains authoritative and cache loss preserves correctness.

## Build checkpoints

### Checkpoint 0 — Measurement plan

Define workloads, stages, warm/cold runs, samples, environment, and thresholds.
Capture baseline first.

### Checkpoint 1 — Aggregation

Build stage p50/p95, totals, queue/wait, and failure reporting. Test math.

### Checkpoint 2 — Availability

Add provider/records, staleness, reason codes, routing integration, and
deterministic state fixtures.

### Checkpoint 3 — Targeted optimization

Select the measured bottleneck, document alternatives, and implement one safe
version-keyed optimization.

### Checkpoint 4 — Before/after verification

Run identical workloads and prove cache/Redis outage falls back safely.

### Checkpoint 5 — Documentation

Update operations, architecture, routing design, performance targets, evidence,
and `PROGRESS.md`.

## Required tests

- latency aggregation;
- availability/staleness;
- unavailable exclusion;
- truthful fallback;
- cache version isolation/invalidation;
- cache/Redis loss;
- behavior equivalence;
- benchmark repeatability.

## Migration impact

Possible availability observations and indexes. Ephemeral cache entries never
become lifecycle/ownership truth.

## Security and safety considerations

- No user content in metric labels.
- Bound cardinality.
- Availability cannot override authorization.
- Stale healthy status expires.
- Cache keys include tenant/version identity.

## Out of scope

- production load certification;
- autoscaling;
- global availability;
- multiple simultaneous optimizations;
- speculative tool execution.

## Definition of done

- [ ] Stage p50/p95 evidence exists.
- [ ] Availability has states and staleness.
- [ ] Routing handles unavailable/unknown truthfully.
- [ ] One measured optimization has before/after evidence.
- [ ] Cache/Redis failure preserves correctness.
- [ ] Tests, benchmarks, and docs pass.

## Suggested commit

`perf(platform): attribute latency and optimize measured bottleneck`

## Earn

You can show which stage was slow, why the optimization was chosen, its measured
impact, and how availability/cache behavior fails safely.
