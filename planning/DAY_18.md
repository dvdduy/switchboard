# Day 18 — Durable Dispatch and Failure Recovery

**Status:** Planned  
**Phase:** Phase 2 — Tool Selection, Observability, and Rollout Safety  
**Prerequisites:** Phase 1 durable workflow plus Days 14–17

## Goal

Close the durable acceptance-to-execution gap with a transactional outbox,
database-backed worker claims/leases, restart recovery, idempotent logical tool
delivery, and explicit unknown-outcome reconciliation.

## Learn

- Persisting a turn is insufficient if dispatch intent can be lost.
- Outbox atomicity solves commit-to-dispatch loss, not every duplicate.
- At-least-once delivery requires idempotent logical operations.
- Leases and compare-and-set claims enable recovery.
- A mutation timeout may be unknown outcome, not safe retry.
- Reconciliation is a tool capability.

## Accepted direction

1. Command transaction writes work state plus outbox.
2. Workers claim due records with PostgreSQL leases.
3. Expired claims are recoverable.
4. Delivery is explicitly at least once.
5. Stable invocation idempotency keys survive attempts.
6. Delivery attempts are separate from logical invocation.
7. Classified failures drive bounded retry/backoff/dead-letter behavior.
8. Crash before dispatch is reclaimable.
9. Confirmed persisted progress resumes safely.
10. Lost mutation response becomes `UNKNOWN_OUTCOME`.
11. A reference reconcilable tool queries by idempotency/operation key:
    found → success; proven absent → safe same-key retry; indeterminate → stop.
12. Never claim exactly once.

## Build checkpoints

### Checkpoint 0 — Failure matrix

Document crash points, durable state, reclaimability, retry safety, and
user-visible outcome.

### Checkpoint 1 — Transactional outbox

Add outbox contract, persistence, migration, atomic creation, due query, and
indexes. Prove committed accepted work has dispatch intent.

### Checkpoint 2 — Worker claim and lease

Implement claim, heartbeat, release, expiry/reclaim, worker identity, and
compare-and-set behavior with short transactions.

### Checkpoint 3 — Delivery attempts and retry taxonomy

Persist attempts, classify outcomes, apply bounded backoff, and preserve logical
idempotency identity.

### Checkpoint 4 — Crash recovery

Inject crashes before dispatch, after dispatch-before-persist, between steps,
and during completion. Restart a new worker.

### Checkpoint 5 — Unknown-outcome reconciliation

Use a deterministic mutation tool keyed by idempotency value. Demonstrate
successful external effect plus lost response does not duplicate the resource.

### Checkpoint 6 — Operations and verification

Add trace/metrics, queue/runbook, graceful shutdown, docs, `PROGRESS.md`, and
full gates.

## Required tests

- atomic work + outbox;
- concurrent claims;
- lease expiry/reclaim/heartbeat;
- duplicate delivery;
- stable idempotency key;
- retry limits;
- crash matrix;
- unknown found/absent/indeterminate;
- no duplicate mutation;
- dead-letter/review;
- database/Redis loss;
- migration/worker shutdown.

## Migration impact

Expected outbox, claim/lease, delivery-attempt, and reconciliation records with
due/status indexes.

## Security and safety considerations

- Outbox contains references, not secrets.
- Revalidate policy/approval/tool lifecycle before mutation.
- Unknown outcomes never blind-retry.
- Reconciliation output is untrusted.
- Bound retry and queue growth.

## Out of scope

- managed broker;
- cross-region ownership;
- exactly-once delivery;
- generic compensation;
- review UI;
- nonreconcilable mutation retry.

## Definition of done

- [ ] Work and dispatch intent commit atomically.
- [ ] Workers claim with recoverable leases.
- [ ] Delivery is explicitly at least once.
- [ ] Stable logical keys survive attempts.
- [ ] Crash recovery resumes persisted work.
- [ ] Lost response enters unknown outcome.
- [ ] Reconciliation prevents duplicate reference mutation.
- [ ] Retry/dead-letter is bounded and observable.
- [ ] Failure, concurrency, worker, migration, and full gates pass.

## Suggested commit

`feat(execution): add durable dispatch and worker recovery`

## Earn

You can explain and prove outbox, leases, at-least-once delivery, stable
idempotency, and unknown-outcome reconciliation.
