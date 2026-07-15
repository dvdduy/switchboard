# Switchboard Progress

## Status

- **Current phase:** Phase 1 — release verification
- **Current milestone:** Milestone 1 implementation complete; release pending
- **Completed through:** Day 10 checkpoint 6
- **Next session:** Day 10 checkpoint 7 — release verification and tag review

## Progress log

| Day | Status | Learn | Build | Validation | Commit | Earn / interview evidence | Debt / follow-up |
|---:|---|---|---|---|---|---|---|
| 0 | Complete | Product/platform boundary; durable execution; offline vs live evaluation | Documentation source of truth and initial ADRs | Cross-document review completed | N/A | Explain Switchboard as shared conversation, tool-execution, and quality-control infrastructure | Keep documents aligned with implementation |
| 1 | Complete | Modular monolith, ports/adapters, API versus worker lifecycle | Python 3.13 scaffold, FastAPI health/readiness, separate worker, PostgreSQL, Redis, Docker, CI | Ruff, mypy, pytest, architecture tests, container build | `chore(scaffold): establish Switchboard API, worker, and local infrastructure` | Explain why durable work is separated from the HTTP process and frameworks remain outside the domain | No product API beyond health endpoints |
| 2 | Complete | Conversation history versus logical turns and physical attempts; transaction and ordering invariants | Versioned agents, conversations, immutable messages, turns, attempts, Alembic, repositories, UoW, atomic `StartConversation` | PostgreSQL migration, rollback, constraint, persistence, and concurrent-append tests | `feat(conversations): add durable conversation and turn model` | Explain deterministic per-conversation ordering, version pinning, and atomic durable turn creation | No execution events, outbox, streaming API, or worker dispatch yet |
| 3 | Complete | SSE as a delivery view over a durable event log | Immutable execution events, deterministic simulator, replay-then-tail service, reconnectable SSE | Ruff, mypy, 103 tests, PostgreSQL integration tests, migration round-trip, container build | Pending approval: `feat(streaming): add durable reconnectable turn event stream` | Explain committed-event replay, exclusive reconnect cursors, and why transport disconnect is not cancellation | No outbox, durable worker recovery, real provider, Redis notification optimization, retention policy, or production chunk tuning |
| 4 | Complete | Context windows as explicit product/reliability budgets; summaries as lossy derived artifacts with provenance | Immutable agent-version context policies, deterministic bounded assembler, durable prefix summaries, compatible reuse workflow | Ruff, mypy, 163 tests, PostgreSQL reconstruction and migration round-trip, container build | Pending approval: `feat(context): add token-budgeted conversation context management` | Explain turn-pinned context snapshots, mandatory recent context, summary provenance, and why summarization runs outside transactions | No production tokenizer or semantic summarizer, summary chaining/retention, large-history optimization, or model-loop integration |
| 5 | Complete | A registry is a versioned safety contract; schema validation does not replace behavioral conformance | Team-owned tool definitions, immutable manifests, separate CAS lifecycle state, conformance history, immutable agent bindings, reference adapters, eligible query | Ruff, mypy, 216 tests, PostgreSQL migration/concurrency coverage, migration round-trip, container build | `d08a776` — `feat(tools): add versioned registry and conformance gates` | Explain immutable content versus mutable lifecycle, exact-version activation evidence, and safe idempotent adapter contracts | No runtime authorization/health filtering, public registry API, production HTTP/MCP/queue adapters, durable dispatch/recovery, or conformance retention/telemetry policy |
| 6 | Complete | Public API contracts require durable acceptance, boundary idempotency, stable errors, and ownership-safe reads | Versioned create/continue commands, command receipts, ordered history, turn inspection, team-aware SSE, strict DTOs and OpenAPI | Ruff, mypy, 284 tests, PostgreSQL contract/concurrency coverage, migration round-trip, container build | Pending approval: `feat(api): add versioned conversation commands and history` | Explain why `202` means durable acceptance rather than execution, how database receipt uniqueness handles retries, and why external-client contracts do not expose persistence models | No production auth, rate limits/quotas, automatic outbox dispatch, durable worker claiming/recovery, or receipt/history retention policy |
| 7 | Complete | Orchestration frameworks coordinate bounded steps but do not own durable platform truth | Provider-independent model/orchestration ports, isolated bounded LangGraph direct/one-tool loop, durable invocation lifecycle, locked read-only dispatch, explicit `RunTurn`, safe tool events | Ruff, mypy, 338 tests, PostgreSQL lifecycle/concurrency/E2E coverage, migration round-trip, container build | Pending approval: `feat(orchestration): add bounded LangGraph read-only agent loop` | Explain framework isolation, the `RUNNING` linearization point, stable logical invocation identity, short transactions, and safe partial failure | Explicit runner, fake provider, one read-only tool call, no semantic router, production identity/health, outbox recovery/retries, or retention policy |
| 8 | Complete | Authorization, policy, confirmation, and execution are distinct durable facts; approval binds an exact action fingerprint | Pure policy matrix, immutable evaluation audit, expiring fingerprint-bound approval, awaiting/cancelled lifecycle, safe approval API, idempotent decisions, atomic resume | Ruff, mypy, 406 tests, PostgreSQL migration/concurrency/security/API/E2E coverage, migration round-trip, container build | Pending approval: `feat(policy): add durable confirmation gate for mutations` | Explain why the model proposes but cannot authorize, how PostgreSQL selects one decision/resume winner, and where the post-dispatch guarantee ends | Development identity/scopes, explicit runner, one tool call, in-memory mutation adapter, no outbox recovery, elevated approval, external-effect execution, or unknown-outcome reconciliation |
| 9 | Complete | Durable orchestration is persisted business progress, not a live coroutine; safe resume and exactly-once external effects are different guarantees | PostgreSQL-owned sequential workflow/steps, committed discovery, frozen exact-plan approval, recreated-runner resume, explicit unknown outcomes, safe workflow events, and multi-turn inspection | Ruff, strict mypy, 458 tests; migration, concurrency, restart, failure-matrix, API/SSE/E2E coverage; container gate unavailable because Docker CLI is absent | Pending approval: `feat(workflows): add durable multi-tool pause and resume` | Explain workflow versus attempt/invocation state, two-phase plan freeze, exact-plan approval, committed restart boundaries, and why unknown effects block retry | Explicit runner; no outbox/claim lease, public workflow runner, generalized plan-decision receipts, reconciliation, parallel DAG, compensation, or in-place replanning |

| 10 | Implementation complete; release pending | Integration is evidence: claims must be bounded by executable contracts, failure behavior, and honest local measurements | Guarded demo environment, read-only and approval workflows, failure/verification harnesses, clean Compose migration ordering, interview evidence | Ruff, strict mypy, 479 tests; 14-case failure matrix; 23-test verification matrix; migration round-trip; clean WSL Compose smoke | Pending checkpoint 7 review: `feat(platform): complete Phase 1 conversation platform` | 60-second walkthrough, five-minute demo, design/failure/safety stories, and quantified Phase 1 evidence | Release gate, reviewed commit, and `v0.1-platform` tag remain; Phase 2 owns routing, tracing, outbox recovery, reconciliation, and rollout |

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
- [x] Shared conversation API
- [x] Orchestration adapter
- [x] Policy and approval
- [x] Phase integration

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
- Treat public command acceptance as a PostgreSQL transaction over the command
  receipt, message, turn, and pending attempt; `202` does not imply execution.
- Use hashed idempotency keys plus canonical request fingerprints, with receipt
  uniqueness as the duplicate-command concurrency authority.
- Require explicit development team context on conversation and event APIs,
  while documenting that it is not production authentication.
- Keep LangGraph behind an application orchestration port; PostgreSQL and
  Switchboard lifecycle/events remain the durable authority.
- Bound Day 7 execution to direct response or one read-only call and use a
  deterministic structured model gateway for reliable local tests.
- Commit invocation intent before dispatch, then lock/revalidate the exact tool;
  the `RUNNING` transition plus `tool.started` is the dispatch linearization point.
- Never hold a transaction across context summarization, model calls, or tool
  calls, and exclude arguments, results, exceptions, prompts, and private
  reasoning from public tool events.
- Treat model/tool text as non-authoritative data; evaluate a versioned policy
  from trusted platform context and retain immutable decision audit.
- Bind mutating approval to team, requester, pinned versions, effect,
  environment, policy version, and canonical arguments; public summaries expose
  field names but no values or digest.
- Persist mutation proposal and pause atomically, then consume approval and
  commit `RUNNING` plus `tool.started` before adapter dispatch.
- Use actor-bound approval command receipts and approval-row locking/CAS as the
  decision and duplicate-resume concurrency authority.
- Persist bounded sequential workflow and step progress in PostgreSQL rather
  than treating a LangGraph checkpoint or live coroutine as business truth.
- Commit discovery intent before search, then atomically freeze the exact
  mutation invocations, action evidence, plan fingerprint, and plan approval
  derived from the committed result.
- Reconstruct the next safe workflow action from immutable terminal evidence;
  use locks/CAS for one logical claim winner and never hold a transaction across
  an adapter call.
- Treat ambiguous post-dispatch mutation results as durable `UNKNOWN` evidence,
  stop later mutations, require review, and do not claim exactly-once effects.

## Known debt

- Development `X-Team-ID` ownership context is not production authentication or
  authorization; rate limits and quotas are also absent.
- Accepted conversation commands are not automatically dispatched because no
  transactional outbox or durable worker claiming exists.
- Command-receipt and public history retention policies are not defined.
- No durable worker claiming or recovery exists yet.
- Event observers poll PostgreSQL before a future Redis notification optimization.
- Orchestrated execution uses a deterministic structured model gateway rather
  than a real model provider; the earlier simulator remains a test utility.
- Execution-event retention and production chunk-size tuning are not defined yet.
- Agent versions do not yet contain prompt, model, tool, router, and policy-bundle configuration.
- Context counting and summarization are deterministic development strategies,
  not production-provider tokenization or semantic summarization.
- Summary chaining, retention/deletion, and large-history performance policy are
  not defined.
- Context reconstruction is connected to the explicit Day 7 orchestration loop,
  but not to a real provider or automatic worker.
- Tool eligibility and dispatch use trusted development scopes and exact locked
  revalidation; production actor authorization and live health are deferred.
- Tool management is application-only; there is no public registry API.
- Only deterministic local reference adapters exist. HTTP, MCP, queue, and real
  external SaaS adapters are not implemented.
- Read-only and confirmation-gated mutating dispatch are implemented. Durable
  invocation recovery/retries and production unknown-outcome reconciliation are
  not; the mutating reference adapter remains in-memory.
- Development actor/team headers and granted scopes are trusted fixtures, not
  production authentication, membership, delegation, or separation of duties.
- Elevated/admin approval, external-side-effect execution, approval
  notifications/batching/retention, and sensitivity-aware summaries are deferred.
- The graph permits only zero or one tool call and has no semantic router,
  while the separate Day 9 platform workflow supports a bounded sequential
  discovery-plus-mutation path. Semantic routing, dynamic replanning, parallel
  branches, and durable framework checkpoints remain absent.
- Workflow execution is explicit; there is no public workflow creation/runner
  API, automatic claim/lease, reconciliation queue, compensation, or in-place
  replanning.
- Workflow-plan decisions are lifecycle-idempotent but do not yet have a
  generalized command receipt preventing cross-approval reuse of one key.
- Conformance history has no retention policy or production telemetry/duration strategy.
- Manifest fields do not include credential configuration, but semantic secret
  scanning of arbitrary descriptions or schema annotations is not implemented.
- Tenant ownership is validated by the application rather than complete composite tenant constraints.
- Message append-only behavior is enforced by domain/repository convention, not database permissions.
