# Requirements

## Prioritization

- **P0:** required for the capstone and core architectural story.
- **P1:** valuable follow-up after the P0 path is stable.
- **P2:** production-scale extension, intentionally deferred.

## Functional requirements

### Conversation and agent management

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-001 | P0 | Create and continue a versioned conversation through a public API. | An external test client completes a multi-turn conversation using only documented endpoints. |
| FR-002 | P0 | Stream committed turn events and assistant output through SSE. | A client reconnects with a cursor and reconstructs the stream without missing committed events. |
| FR-003 | P0 | Define immutable `AgentVersion` records containing prompt, model, tools, policies, routing configuration, and budgets. | Editing an agent creates a new version; old conversations remain reproducible. |
| FR-004 | P0 | Pin each conversation to an `AgentVersion`. | New versions do not silently alter existing conversations. |
| FR-005 | P0 | Manage context within a defined token budget using truncation and/or summarization. | Long-conversation tests remain within budget, preserve configured mandatory context, and represent omitted history with a provenance-bearing summary. |

Day 6 evidence for FR-001 and FR-002: documented `/api/v1` create and continue
commands, PostgreSQL-backed idempotent acceptance, ordered exclusive-cursor
history, safe turn reads, returned reconnectable event URLs, team-aware SSE
preflight, and an external-client PostgreSQL test that explicitly executes an
accepted turn before consuming its terminal stream. Automatic dispatch remains
deferred because no transactional outbox or durable worker claiming exists.

Day 7 strengthens FR-002 by replaying the committed read-only tool lifecycle and
assistant output through the same ordered reconnectable SSE contract.

Day 4 evidence for FR-005: immutable agent-version context policies, explicit
budget failures, deterministic newest-suffix selection, provenance-bearing
durable prefix summaries, turn-pinned message cutoffs, compatible-summary reuse,
randomized budget tests, and PostgreSQL reconstruction tests. Phase 1 mandatory
context means the current input plus the configured newest-message floor;
semantic critical-fact detection and user-pinned memory are not implemented.

### Tool platform

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-010 | P0 | Register immutable, versioned tool manifests without platform code changes. | A new reference tool is activated through the registration contract alone. |
| FR-011 | P0 | Validate tool schemas, effect type, scopes, timeout, ownership, and idempotency declarations. | Invalid manifests fail with structured diagnostics. |
| FR-012 | P0 | Support tool states `DRAFT`, `ACTIVE`, `DEPRECATED`, and `DISABLED`. | Disabled versions are not routed; historical executions remain readable. |
| FR-013 | P0 | Run a tool conformance suite before activation. | A deliberately malformed tool cannot become active. |
| FR-014 | P0 | Exclude unauthorized or unhealthy tools before semantic routing. | The routing trace records why a preferred tool was unavailable. |

Day 5 evidence for FR-010 through FR-013: team-scoped definitions, lock-allocated
immutable versions, bounded Draft 2020-12 manifest validation with safe
diagnostics, separate compare-and-set lifecycle state, persisted case-level
conformance, exact successful-run activation, immutable agent-version cloning,
and two reference adapters registered without routing changes. Day 5 implements
only the binding/team/lifecycle/conformance portion of FR-014; actor
authorization, health filtering, routing traces, and semantic routing remain
future work.

Day 7 partially strengthens FR-014: explicit execution accepts a trusted
development scope set, filters to active bound read-only versions, and locks and
revalidates the exact version before dispatch. This is not production actor
authorization, live-health filtering, or a semantic routing trace.

### Routing and orchestration

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-020 | P0 | Retrieve candidate tools semantically and apply a final structured selection step. | A golden routing dataset produces measured top-1 and top-k results. |
| FR-021 | P0 | Return explicit routing outcomes: selected, no match, ambiguous, needs clarification, unavailable, or unauthorized. | Every routing attempt ends in one documented outcome. |
| FR-022 | P0 | Attach calibrated confidence and candidate scores to routing decisions. | Reports include accepted coverage, fallback rate, and false-execution rate. |
| FR-023 | P0 | Support multi-step workflows and pauses for approval. | An interrupted workflow resumes from persisted state rather than restarting completed steps. |
| FR-024 | P1 | Run candidate routers in shadow mode without executing their proposed tools. | Current and candidate routing choices are compared on live-like traffic. |

Day 7 does not claim FR-020 through FR-024. It supplies a bounded orchestration
adapter over exact eligible descriptors and deterministic structured
`Respond`/`CallTool` actions, without semantic retrieval, calibrated confidence,
multi-step planning, or persisted pauses.

### Policies and safety

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-030 | P0 | Classify tools as `READ_ONLY`, `MUTATING`, `EXTERNAL_SIDE_EFFECT`, or `PRIVILEGED`. | Policy behavior derives from the manifest rather than tool-name conventions. |
| FR-031 | P0 | Evaluate user, team, agent, tool, scopes, environment, and arguments before execution. | Policy decisions are versioned and included in the trace. |
| FR-032 | P0 | Require explicit confirmation for configured mutating actions. | A mutation remains inert before approval and executes exactly once logically after approval. |
| FR-033 | P0 | Treat tool output and retrieved content as untrusted input. | Malicious tool output cannot alter platform permissions or bypass confirmation. |
| FR-034 | P0 | Record an audit trail for mutating and privileged operations. | The audit record identifies requester, approver, policy, tool version, arguments summary, and outcome. |

Day 8 evidences the Phase 1 subset of FR-030 through FR-034: a pure versioned
policy matrix allows valid read-only calls, requires fingerprint-bound durable
confirmation for valid mutations, denies external-side-effect and privileged
effects, treats model/tool text as non-authoritative data, and persists safe
requester/approver/policy/tool/outcome audit. Production identity, elevated
approval, automatic durable dispatch, and unknown-outcome reconciliation remain
deferred.

### Durable execution

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-040 | P0 | Persist a `TurnExecution` state machine and append-only `ExecutionEvent` history. | Invalid transitions are rejected and all valid transitions are reconstructable. |
| FR-041 | P0 | Commit turn creation and dispatch intent atomically using an outbox or equivalent. | A crash between API commit and worker dispatch cannot permanently lose a turn. |
| FR-042 | P0 | Use stable idempotency keys for logical tool invocations. | Worker retries reuse the original key and do not create duplicate effects with a conforming tool. |
| FR-043 | P0 | Classify tool outcomes including validation, unauthorized, rate-limited, retriable, terminal, timeout, and unknown outcome. | The orchestrator has explicit behavior for every category. |
| FR-044 | P0 | Reconcile unknown outcomes before retrying external mutations. | A simulated lost response after successful mutation does not produce a duplicate resource. |
| FR-045 | P0 | Distinguish client disconnect, explicit cancellation, and worker failure. | Disconnecting an SSE client does not implicitly cancel an approved mutation. |

Day 7 strengthens FR-040 and partially evidences FR-042/FR-043 with durable
tool-invocation identity, a stable platform-generated key, compare-and-set
lifecycle transitions, and safe success/failure events. It intentionally does
not claim retry recovery, the full outcome taxonomy, outbox delivery, or unknown
outcome reconciliation.

### Evaluation and release

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-050 | P0 | Version eval datasets, cases, evaluators, judge configuration, and runtime configuration. | Any eval result can identify all inputs needed for reproduction. |
| FR-051 | P0 | Run deterministic evaluators before LLM-based judges. | Tool, policy, schema, and confirmation checks do not depend on an LLM judge. |
| FR-052 | P0 | Compare candidate results with a baseline and fail CI on configured regression. | A deliberately bad change is blocked with case-level diagnostics. |
| FR-053 | P0 | Calibrate LLM judges against a small human-labelled set. | Judge disagreement or variance is visible in the report. |
| FR-054 | P0 | Roll releases through offline evaluation, shadow, canary, and full stages. | A candidate cannot skip a required stage. |
| FR-055 | P0 | Automatically roll back a canary on a configured live regression signal. | An induced error or latency regression returns traffic to the baseline version. |
| FR-056 | P1 | Queue low-confidence, unknown-outcome, and evaluator-disagreement cases for review. | Reviewed cases can be promoted into a new dataset version. |

### Observability and operations

| ID | Priority | Requirement | Acceptance signal |
|---|---:|---|---|
| FR-060 | P0 | Trace structured execution events, versions, candidate scores, policies, tool calls, errors, tokens, cost, and latency. | A reported turn can be diagnosed from one correlated trace. |
| FR-061 | P0 | Replay recorded execution history without re-executing external side effects by default. | Replay is safe and deterministic over stored events. |
| FR-062 | P0 | Attribute latency to routing, model generation, tool execution, persistence, and queue time. | Performance reports identify the dominant stage. |
| FR-063 | P1 | Enforce per-agent token, request, concurrency, and cost quotas. | One agent cannot exhaust all local worker capacity. |
| FR-064 | P1 | Apply bounded queues, backpressure, and explicit load shedding. | Overload produces defined queueing or rejection behavior instead of unbounded memory growth. |

## Non-functional requirements

| ID | Priority | Requirement |
|---|---:|---|
| NFR-001 | P0 | PostgreSQL is the durable source of truth for conversations, execution, configuration, and eval metadata. |
| NFR-002 | P0 | Redis loss may reduce performance but must not destroy committed conversation or execution state. |
| NFR-003 | P0 | All externally visible APIs and manifests are versioned. |
| NFR-004 | P0 | Critical operations are idempotent or explicitly declared non-idempotent with restrictive retry policy. |
| NFR-005 | P0 | Sensitive values are redacted from logs, traces, and eval exports. |
| NFR-006 | P0 | Durable architectural decisions are recorded as ADRs. |
| NFR-007 | P0 | The full P0 system runs through Docker Compose without proprietary infrastructure. |
| NFR-008 | P0 | CI performs linting, type checking, unit/integration tests, contract tests, and the eval regression gate. |
| NFR-009 | P0 | Core runtime code is independent of a specific LLM or orchestration framework through ports/adapters. |
| NFR-010 | P0 | No component claims exactly-once delivery across arbitrary external systems. |
| NFR-011 | P1 | The platform exposes per-agent SLO measurements for latency, completion, fallback, policy denial, and unknown outcomes. |
| NFR-012 | P2 | The architecture can later separate control-plane and data-plane deployment without changing public contracts. |

Day 7 evidence for NFR-009 includes architecture tests that restrict LangGraph
to its adapter and provider/framework-independent application ports exercised by
deterministic fakes.

## Initial performance targets

These are learning-project targets and may be adjusted through benchmarks:

- API turn acceptance p95: under 250 ms locally, excluding model/tool execution.
- Time to first simulated stream event p95: under 500 ms locally.
- Router candidate retrieval p95: under 150 ms for the reference registry.
- No duplicate logical mutation in the crash/retry integration scenario.
- All committed SSE events recoverable after reconnect.
- Canary rollback decision within 60 seconds in the local simulation.

## Requirement-change process

1. Open or update a requirement with motivation and acceptance criteria.
2. Decide whether an ADR is needed.
3. Update affected use cases, architecture, tests, and delivery plan.
4. Record the change in `PROGRESS.md`.
5. Do not expand P0 scope during a development session without explicitly trading off another P0 item.
