# Phase 1 Evidence and Interview Guide

## Honest capability boundary

Phase 1 delivers a framework-independent conversation foundation with durable
PostgreSQL state, reconnectable SSE, bounded context, immutable/conformant tool
versions, explicit policy and approval, and a restart-safe bounded sequential
workflow. The API durably accepts commands, but execution is still invoked by a
trusted development runner. There is no transactional outbox, automatic worker
claiming, semantic router, production identity, real model provider, or
unknown-outcome reconciliation.

## Quantified evidence

The following is local development evidence, not a production capacity claim.

| Evidence | Result |
|---|---:|
| Full automated suite | 479 passed |
| Focused contract/operability matrix | 23 passed |
| Focused failure/recovery matrix | 14 passed |
| Clean Compose smoke | migration, API, worker, live, and ready passed |
| Read-only API acceptance | 37.312 ms, sample size 1 |
| First committed event observed | 29.306 ms after runner start, sample size 1 |
| Read-only journey total | 202.845 ms, sample size 1 |
| Approval workflow total | 479.945 ms, sample size 1 |
| Bounded context | 42 of 3,328 available input tokens |
| Mutation calls | 2 planned, 2 first resume, 0 duplicate resume |
| SSE replay | sequences 1–9 reconstructed without gaps or duplicates |

Measured on the local deterministic environment on 2026-07-14. Timings vary by
machine and include in-process HTTP plus local PostgreSQL work. The first-event
value is an observed polling upper bound, not an instrumented production
percentile.

## 60-second walkthrough

“Switchboard is shared infrastructure beneath chat and agent products. The API
atomically accepts an idempotent conversation command into PostgreSQL and
returns a reconnectable event URL. A trusted runner reconstructs the turn from
its pinned agent version, builds context within an explicit token budget, and
runs a bounded LangGraph adapter through framework-independent ports. Tool
versions are immutable, conformant, exactly bound, and revalidated before
dispatch. Read-only work runs directly; mutations pause behind an expiring
fingerprint-bound approval. The multi-tool example commits discovery, freezes
an exact ordered plan, survives runner recreation, and skips completed work.
SSE is only a delivery view over committed events, so reconnect is safe and a
disconnect is not cancellation. PostgreSQL owns correctness; Redis and
LangGraph are replaceable adapters. I deliberately do not claim exactly-once
external effects: ambiguous mutations become unknown, stop the workflow, and
require reconciliation.”

## Five-minute demo

1. Reset and seed deterministic state:

   ```powershell
   uv run switchboard-demo-environment reset
   uv run switchboard-demo-environment seed
   ```

   Point out stable synthetic identities, active exact tool bindings, full
   conformance, and the absence of paid credentials.

2. Run `uv run switchboard-demo read-only`. Show durable `202` acceptance,
   bounded context, tool events before output, disconnect/reconnect sequences,
   reconstructed response, ordered history, and explicitly local timing.

3. Run `uv run switchboard-demo approval-workflow`. Show committed discovery,
   value-free exact-plan approval, recreated runner, two mutations, zero calls
   on duplicate resume, terminal SSE, and four-message history.

4. Run `uv run switchboard-demo-failures --list`. Contrast automatic replay,
   explicit fresh-command recovery, and manual reconciliation for unknown
   external outcomes.

5. Run `uv run switchboard-demo-verify --list`. Close with the OpenAPI,
   migrations, redaction, bounds, health, and Compose evidence, then state the
   Phase 2 gaps rather than implying production completeness.

## Design deep dive

- **Durable authority:** PostgreSQL owns conversations, lifecycle, events,
  approvals, workflow steps, and idempotency receipts. LangGraph coordinates a
  bounded in-process segment; Redis is not required for correctness.
- **Transaction boundaries:** intent and lifecycle transitions commit before
  model/tool calls. No transaction spans an external call. Each workflow result
  commits before the next step is selected.
- **Concurrency:** row locks, uniqueness constraints, and compare-and-set
  updates select one message sequence, command result, approval decision,
  invocation claim, and workflow finalizer.
- **Versioning:** turns pin immutable agent versions; tool content is immutable
  while lifecycle state changes separately. Approval fingerprints bind exact
  versions, policy, ownership, environment, and canonical arguments.
- **Delivery versus execution:** SSE cursors replay committed events. Transport
  disconnect never mutates execution state, and public `202` acceptance does
  not imply that execution was automatically dispatched.

## Failure story

The hardest boundary is a lost response after a mutation may have reached an
external system. Retrying would risk a duplicate effect, while declaring
failure would be dishonest. Switchboard commits invocation intent and
`tool.started` before dispatch. If the result is ambiguous, the invocation and
workflow step become `UNKNOWN`, later mutations are skipped, the workflow ends
`REVIEW_REQUIRED`, and duplicate resume replays terminal evidence without
calling the adapter. Phase 2 must reconcile using the stable idempotency key
before any deliberate retry.

## Safety story

The model proposes but never authorizes. Eligibility uses trusted ownership,
exact binding, lifecycle, conformance, and scopes. A pure versioned policy
allows read-only calls, requires confirmation for mutations, and denies
external-side-effect or privileged calls in Phase 1. Approval exposes field
names but not values or digests and binds the exact frozen action. Tool/model
content remains untrusted data, public events exclude arguments/results/private
reasoning, and cross-team reads use the same response as unknown resources.

## Phase 2 handoff

1. Write the routing design and define selected/no-match/ambiguous outcomes.
2. Add semantic candidate retrieval, structured final selection, confidence,
   and a golden routing evaluation dataset.
3. Add correlated tracing and stage-level latency attribution without exposing
   sensitive values or private reasoning.
4. Implement transactional outbox dispatch, bounded worker leases, and safe
   pre-dispatch recovery.
5. Add explicit unknown-outcome reconciliation before mutation retry.
6. Exercise shadow/canary rollout only after routing and execution signals are
   trustworthy.

Retain the Phase 1 contracts: PostgreSQL authority, short transactions, stable
logical keys, immutable audit, reconnectable events, and no exactly-once claim.
