# Switchboard Progress

## Status

- **Current phase:** Phase 1 — Conversation Platform Foundations
- **Current milestone:** Milestone 1 — Conversation platform
- **Completed through:** Day 5
- **Next session:** Day 6 — Shared conversation API

## Progress log

| Day | Status | Learn | Build | Validation | Commit | Earn / interview evidence | Debt / follow-up |
|---:|---|---|---|---|---|---|---|
| 0 | Complete | Product/platform boundary; durable execution; offline vs live evaluation | Documentation source of truth and initial ADRs | Cross-document review completed | N/A | Explain Switchboard as shared conversation, tool-execution, and quality-control infrastructure | Keep documents aligned with implementation |
| 1 | Complete | Modular monolith, ports/adapters, API versus worker lifecycle | Python 3.13 scaffold, FastAPI health/readiness, separate worker, PostgreSQL, Redis, Docker, CI | Ruff, mypy, pytest, architecture tests, container build | `chore(scaffold): establish Switchboard API, worker, and local infrastructure` | Explain why durable work is separated from the HTTP process and frameworks remain outside the domain | No product API beyond health endpoints |
| 2 | Complete | Conversation history versus logical turns and physical attempts; transaction and ordering invariants | Versioned agents, conversations, immutable messages, turns, attempts, Alembic, repositories, UoW, atomic `StartConversation` | PostgreSQL migration, rollback, constraint, persistence, and concurrent-append tests | `feat(conversations): add durable conversation and turn model` | Explain deterministic per-conversation ordering, version pinning, and atomic durable turn creation | No execution events, outbox, streaming API, or worker dispatch yet |
| 3 | Complete | SSE as a delivery view over a durable event log | Immutable execution events, deterministic simulator, replay-then-tail service, reconnectable SSE | Ruff, mypy, 103 tests, PostgreSQL integration tests, migration round-trip, container build | Pending approval: `feat(streaming): add durable reconnectable turn event stream` | Explain committed-event replay, exclusive reconnect cursors, and why transport disconnect is not cancellation | No outbox, durable worker recovery, real provider, Redis notification optimization, retention policy, or production chunk tuning |
| 4 | Complete | Context windows as explicit product/reliability budgets; summaries as lossy derived artifacts with provenance | Immutable agent-version context policies, deterministic bounded assembler, durable prefix summaries, compatible reuse workflow | Ruff, mypy, 163 tests, PostgreSQL reconstruction and migration round-trip, container build | Pending approval: `feat(context): add token-budgeted conversation context management` | Explain turn-pinned context snapshots, mandatory recent context, summary provenance, and why summarization runs outside transactions | No production tokenizer or semantic summarizer, summary chaining/retention, large-history optimization, or model-loop integration |
| 5 | Complete | A registry is a versioned safety contract; schema validation does not replace behavioral conformance | Team-owned tool definitions, immutable manifests, separate CAS lifecycle state, conformance history, immutable agent bindings, reference adapters, eligible query | Ruff, mypy, 216 tests, PostgreSQL migration/concurrency coverage, migration round-trip, container build | Pending approval: `feat(tools): add versioned registry and conformance gates` | Explain immutable content versus mutable lifecycle, exact-version activation evidence, and safe idempotent adapter contracts | No runtime authorization/health filtering, public registry API, production HTTP/MCP/queue adapters, durable dispatch/recovery, or conformance retention/telemetry policy |

## Milestones

### Milestone 0 — Documentation and readiness

- [x] Project overview
- [x] Requirements
- [x] Architecture
- [x] Use cases
- [x] Domain model
- [x] Security and policy model
- [x] Evaluation strategy
- [x] Testing strategy
- [x] Operations model
- [x] Delivery plan
- [x] Initial ADRs
- [x] Developer review and accepted changes
- [x] Day 1 technology decisions

### Milestone 1 — Conversation platform

- [x] Scaffold and CI
- [x] Persistence and migrations
- [x] Versioned agents and conversations
- [x] Durable conversation, turn, and attempt model
- [x] Durable execution-event model
- [x] SSE streaming
- [x] Context-window management
- [x] Tool registry and conformance
- [ ] Shared conversation API
- [ ] Orchestration adapter
- [ ] Policy and approval
- [ ] Phase integration

### Milestone 2 — Routing and reliable execution

- [ ] Routing design doc
- [ ] Hybrid router
- [ ] Routing eval
- [ ] Structured tracing and replay
- [ ] Latency attribution
- [ ] Outbox and worker recovery
- [ ] Unknown-outcome reconciliation
- [ ] Shadow and canary rollout

### Milestone 3 — Evaluation and capstone

- [ ] Versioned eval platform
- [ ] Automated scoring
- [ ] CI regression gate
- [ ] Reporting view
- [ ] Simplification ADR
- [ ] Mentoring artifacts
- [ ] Full test strategy
- [ ] Final pipeline and walkthrough

## Accepted implementation decisions

- Start as a modular monolith with separate API and worker processes.
- Keep domain code independent from FastAPI, SQLAlchemy, Redis, and orchestration frameworks.
- Use PostgreSQL as the durable source of truth and Redis only as an optimization.
- Model conversation history separately from logical turn execution and physical attempts.
- Order messages with a positive per-conversation sequence allocated under a row lock.
- Store a default agent version on the conversation and pin the actual version on every turn.
- Require explicit unit-of-work commit for multi-record application transactions.
- Do not claim exactly-once effects across arbitrary external systems.
- Persist immutable public execution events with turn-local sequences allocated
  under a PostgreSQL row lock.
- Treat SSE as a replayable delivery view over committed PostgreSQL events, with
  `Last-Event-ID` as an exclusive cursor.
- Poll with short independent transactions; Redis notification remains an
  optional future latency optimization rather than a correctness dependency.
- Pin typed context budgets to immutable agent versions and reconstruct a turn
  only through its input-message sequence.
- Treat summaries as immutable derived prefix artifacts with explicit coverage
  and version provenance, never as visible messages or authorization evidence.
- Run summarization outside database transactions and use a uniqueness-backed
  insert/re-read to select one concurrent authority winner.
- Keep validated tool manifest content immutable while lifecycle state changes
  separately through revision compare-and-set updates.
- Require successful conformance for the exact version before activation and
  clone agent versions when adding exact tool-version bindings.

## Known debt

- The public API exposes health/readiness and read-only turn-event SSE; public
  create/continue-conversation commands do not exist yet.
- No transactional outbox exists yet.
- No durable worker claiming or recovery exists yet.
- Event observers poll PostgreSQL before a future Redis notification optimization.
- Durable execution uses a deterministic simulator rather than a real model provider.
- Execution-event retention and production chunk-size tuning are not defined yet.
- Agent versions do not yet contain prompt, model, tool, router, and policy-bundle configuration.
- Context counting and summarization are deterministic development strategies,
  not production-provider tokenization or semantic summarization.
- Summary chaining, retention/deletion, and large-history performance policy are
  not defined.
- Context reconstruction is not yet connected to a real model orchestration loop.
- Tool eligibility currently covers binding, team ownership, active lifecycle,
  and successful conformance; runtime actor authorization and health are deferred.
- Tool management is application-only; there is no public registry API.
- Only deterministic local reference adapters exist. HTTP, MCP, queue, and real
  external SaaS adapters are not implemented.
- Tool dispatch, durable invocation recovery, and production unknown-outcome
  reconciliation are not implemented; the mutating reference adapter is in-memory.
- Conformance history has no retention policy or production telemetry/duration strategy.
- Manifest fields do not include credential configuration, but semantic secret
  scanning of arbitrary descriptions or schema annotations is not implemented.
- Tenant ownership is validated by the application rather than complete composite tenant constraints.
- Message append-only behavior is enforced by domain/repository convention, not database permissions.
