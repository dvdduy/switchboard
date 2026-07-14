# Testing Strategy

## Testing goals

The suite must prove domain invariants, transaction boundaries, failure recovery, public contracts, routing quality, and release safety—not only happy-path endpoint behavior.

## Test layers

### Unit tests

- value objects and manifest validation;
- state-machine transitions;
- policy rules;
- routing outcome interpretation;
- retry classification;
- approval argument fingerprints;
- context-budget logic;
- metric calculations.

### Application tests

Use in-memory/fake ports to verify orchestration:

- create turn and enqueue intent;
- pause/resume for approval;
- direct answer versus tool path;
- no-match and clarification;
- unknown-outcome handling;
- release-stage transitions.

### Persistence integration tests

Against PostgreSQL:

- migrations upgrade and downgrade where supported;
- transactional turn + outbox creation;
- worker claim and lease behavior;
- uniqueness of logical invocation/idempotency key;
- append-only event sequence;
- concurrent approval or worker races;
- restart recovery.

### Tool contract tests

Every tool adapter must pass:

- manifest/schema compatibility;
- valid input/output;
- malformed input;
- timeout behavior;
- error mapping;
- idempotency declaration behavior;
- reconciliation behavior when required;
- redaction rules.

Day 5 implements this boundary with deterministic reference adapters and a
framework-independent eight-case runner. PostgreSQL tests additionally cover
team-key and version-allocation races, complete-run rollback, exact activation
ownership, lifecycle compare-and-set conflicts, immutable agent-version cloning,
binding eligibility, and exclusion of failed/deprecated/disabled/cross-team
records. Production external-adapter failure injection remains deferred.

### API contract tests

- versioned request/response schemas;
- stable error codes;
- approval endpoints;
- pagination/history;
- SSE event types, IDs, and reconnect behavior.

### Property tests

Useful invariants:

- invalid state transitions are never accepted;
- event sequence is monotonic;
- approved argument fingerprint changes when meaningful arguments change;
- repeated delivery of one logical invocation preserves its idempotency key;
- replay of committed events reconstructs the same derived state;
- context output never exceeds its budget.

### Failure-injection tests

Simulate:

- API crash after database commit;
- worker crash before tool dispatch;
- worker crash after tool success but before persistence;
- lost response causing unknown outcome;
- PostgreSQL transient failure;
- Redis loss;
- duplicate dispatch;
- slow and rate-limited tools;
- model timeout or malformed structured output;
- SSE disconnect and reconnect;
- stale registry cache;
- tool disabled between routing and dispatch.

### Eval tests

- dataset schema and version immutability;
- deterministic evaluator correctness;
- known-good/known-bad judge calibration examples;
- baseline comparison logic;
- case-level regression reporting;
- critical safety threshold behavior.

### Rollout simulation tests

- required stage order;
- shadow never executes side effects;
- canary traffic allocation;
- metric-window calculation;
- automatic rollback;
- rollback idempotency;
- baseline restoration.

## CI pipeline

```text
1. dependency and formatting checks
2. lint
3. type check
4. unit tests
5. PostgreSQL/Redis integration tests
6. API and tool contract tests
7. failure-injection smoke suite
8. deterministic eval suite
9. bounded LLM eval suite when credentials/environment permit
10. Docker Compose smoke test
```

The repository should provide a deterministic local mode so contributors can run CI without paid model calls. Model-backed evaluation may be a separate protected job.

## Test data

- synthetic project/task domain;
- fixed identities and team namespaces;
- deterministic fake model and embedding ports;
- controllable tools: read-only, mutating, slow/flaky, unauthorized, malicious-output, and reconcilable unknown-outcome tool.

## Definition of done for a feature

A feature is not done until:

1. invariants and failure behavior are documented;
2. unit or application tests cover core behavior;
3. integration tests cover persistence/adapter boundaries;
4. telemetry and error classification exist;
5. affected docs and `PROGRESS.md` are updated;
6. the project-wide check command passes.
