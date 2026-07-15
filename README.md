# Switchboard

Switchboard is shared backend infrastructure for AI chat and agent products.

The current implementation provides:

- versioned conversations and agents;
- durable logical turns and physical attempts;
- immutable PostgreSQL execution events with deterministic per-turn ordering;
- deterministic simulated assistant execution;
- reconnectable read-only SSE replay and tailing;
- turn-pinned, token-budgeted context assembly with durable prefix summaries;
- team-owned immutable tool manifests with deterministic conformance;
- exact-version agent bindings and an active-bound eligible-tool query;
- deterministic read-only and idempotent mutating reference adapters;
- a framework-isolated bounded LangGraph loop with deterministic model actions;
- durable explicit turn execution through either a direct response or one
  active, bound, scoped tool invocation;
- PostgreSQL-owned bounded sequential workflows with persisted discovery,
  frozen exact mutation plans, one plan-level approval, recreated-runner resume,
  and truthful terminal summaries;
- a pure policy matrix with durable audit, fingerprint-bound mutating approval,
  expiring confirmation, safe resume, and cancellation;
- a versioned conversation API with durable idempotent commands, ordered
  history reads, turn inspection, safe approval reads/decisions, and team-aware
  reconnectable SSE.

Planned capabilities include:

- semantic tool routing;
- automatic durable worker dispatch and recovery;
- evaluation and regression detection;
- observability and rollout safety.

## Development requirements

- Python 3.13
- uv
- Docker
- Docker Compose
- GNU Make, or run the underlying uv commands directly

## Install dependencies

```bash
uv sync
```

## Local infrastructure

Start PostgreSQL and Redis:

```bash
docker compose up -d postgres redis
```

## PostgreSQL integration tests

Start the disposable integration database:

```bash
docker compose --profile test up -d postgres-test
```

Apply database migrations:

```bash
uv run alembic upgrade head
```

## Run the API

```bash
docker compose up --build api worker
```

The public conversation API uses an explicit `X-Team-ID` UUID as development
identity. This is not production authentication. Create and continue commands
also require an opaque `Idempotency-Key` of 1–128 visible ASCII characters.
The key is hashed before persistence.

Approval decisions additionally require `X-Actor-ID`. Both identity headers are
trusted development context, not authentication or membership proof.

Create a conversation using an agent version already registered through the
application workflow:

```bash
curl -i -X POST \
  -H "Content-Type: application/json" \
  -H "X-Team-ID: <team-id>" \
  -H "Idempotency-Key: create-001" \
  -d '{"agent_version_id":"<agent-version-id>","initial_user_message":"Which Project Alpha tasks are overdue?"}' \
  http://127.0.0.1:8000/api/v1/conversations
```

The API returns `202 Accepted` only after the conversation, user message,
received turn, pending attempt, and command receipt commit atomically. It does
not start execution. Repeating the same command with the same key returns the
original identifiers; reusing the key for different content returns `409`.

Continue and inspect a conversation:

```bash
curl -i -X POST \
  -H "Content-Type: application/json" \
  -H "X-Team-ID: <team-id>" \
  -H "Idempotency-Key: continue-001" \
  -d '{"user_message":"Only include tasks assigned to me."}' \
  http://127.0.0.1:8000/api/v1/conversations/<conversation-id>/turns

curl -H "X-Team-ID: <team-id>" \
  "http://127.0.0.1:8000/api/v1/conversations/<conversation-id>/messages?after_sequence=0&limit=50"

curl -H "X-Team-ID: <team-id>" \
  http://127.0.0.1:8000/api/v1/turns/<turn-id>
```

Given an existing turn ID created through the application/persistence workflow,
observe its committed events:

```bash
curl -N \
  -H "Accept: text/event-stream" \
  -H "X-Team-ID: <team-id>" \
  http://127.0.0.1:8000/api/v1/turns/<turn-id>/events
```

Reconnect after sequence 3 using the exclusive cursor:

```bash
curl -N \
  -H "Accept: text/event-stream" \
  -H "X-Team-ID: <team-id>" \
  -H "Last-Event-ID: 3" \
  http://127.0.0.1:8000/api/v1/turns/<turn-id>/events
```

Frames use the durable turn-local sequence as `id`, a stable platform event name
as `event`, and a compact JSON payload as `data`. Disconnecting the observer does
not cancel or mutate the turn.

Read and decide an approval:

```bash
curl -H "X-Team-ID: <team-id>" \
  http://127.0.0.1:8000/api/v1/approvals/<approval-id>

curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Team-ID: <team-id>" \
  -H "X-Actor-ID: <actor-id>" \
  -H "Idempotency-Key: approve-001" \
  -d '{"decision":"approve"}' \
  http://127.0.0.1:8000/api/v1/approvals/<approval-id>/decisions
```

Approval responses expose stable identities, lifecycle timestamps, fingerprint
version, and argument field names only—not argument values or the digest. The
additive `target_type` distinguishes `invocation` from `workflow_plan`; plan
responses include ordered safe actions and a mutation count without executable
values.

## Context management

Each immutable agent version declares its model-window budget, reserved output,
fixed instruction/tool overhead, maximum summary size, and minimum recent
message count. Context reconstruction reads only through the selected turn's
input message, preserves the current input and configured recent floor, and
uses a durable provenance-bearing summary for an omitted older prefix.

The counter and summarizer boundaries are provider-independent. The included
summarizer is deterministic and extractive for development and tests; it is not
a production model tokenizer or semantic-memory system. Context reconstruction
is consumed by the explicit run-turn workflow and by follow-up turns inspecting
Day 9 workflow summaries. It is not exposed as a
public endpoint, and the model gateway remains a deterministic structured fake
rather than a real provider.

## Tool registry

Day 5 adds an application-level control plane for registering team-owned tool
definitions, publishing validated immutable manifests, running deterministic
conformance, activating exact versions, and cloning agent versions with exact
tool bindings. JSON Schema Draft 2020-12 validation is bounded, remote references
are rejected, and diagnostics do not copy rejected values.

The included `search_work_items` and `update_due_date` adapters are deterministic
local examples. Read-only calls dispatch directly after policy evaluation.
Mutating calls pause durably and dispatch only after matching unexpired approval.
External-side-effect and privileged tools remain denied. Adapter state is
intentionally in-memory. No public tool-management endpoint, semantic router,
production authentication/authorization, live-health filter, or production
adapter exists yet.

## Explicit Day 8 execution and approval

The conversation API still returns `202` after durable acceptance and never
starts execution. The application-level `RunTurn` workflow must be invoked by a
trusted development runner or test with the accepted turn, pending attempt,
team, and granted scopes. It builds the pinned bounded context and runs either:

```text
Respond -> response.delta* -> turn.completed
CallTool -> tool.started -> tool.completed -> response.delta* -> turn.completed
Mutating CallTool -> approval.required -> durable pause
Approve -> approval.resolved -> tool.started -> tool.completed -> turn.completed
Reject/expire -> approval.resolved -> turn.cancelled
```

Tool failures emit `tool.failed` with a safe code before the turn closes with
`turn.failed`. Tool events never contain arguments, outputs, provider errors, or
private reasoning. There is intentionally no CLI or HTTP execution command yet;
automatic claiming and crash recovery require the future transactional outbox.

## Day 9 durable multi-tool walkthrough

The Day 9 reference request is: “Find overdue critical tasks, move them to
Friday, and summarize the changes.” There is still no public execution endpoint;
a trusted development runner or test invokes the application workflows:

```text
RunWorkflowDiscovery
  persist discovery intent -> tool.started -> search -> tool.completed

FreezeWorkflowMutationPlan
  validate committed result -> persist exact ordered mutations
  -> workflow.planned -> approval.required -> durable pause

POST /api/v1/approvals/<plan-approval-id>/decisions
  approval.resolved; approval makes the plan resumable but does not run it

RunApprovedWorkflow in a recreated runner/UoW
  workflow.resumed
  -> tool.started/tool.completed for each next pending mutation
  -> workflow.terminal -> turn.completed or turn.failed
```

Each external action has a stable invocation identity and committed intent
before dispatch. The frozen plan cannot gain, remove, reorder, or rewrite
mutations after approval. Completed steps are skipped on recreation. A known
failure stops later mutations; an ambiguous post-dispatch outcome is recorded as
unknown, produces a review-required workflow summary, and is never blindly
retried. Rejection or expiry cancels all never-dispatched mutations.

The workflow events expose IDs, lifecycle values, and counts only. They exclude
search results, mutation arguments, tool output, fingerprints, exceptions,
prompts, and private reasoning. `workflow.terminal` closes workflow progress but
does not close SSE; the following terminal turn event closes the stream.

## Quality gate

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
docker build --tag switchboard:local .
```

The current phase intentionally does not include a transactional outbox,
durable worker claiming/recovery, a real model provider, Redis event
notification, event or summary retention policies, production chunk-size
tuning, production tokenizers, semantic summarization, or summary chaining.
Tool-registry debt also includes production HTTP/MCP/queue adapters, durable
dispatch recovery, production authorization and health filtering, and
conformance retention/telemetry policy.
Ordinary confirmation does not enable privileged or external-side-effect tools,
and approval does not make ambiguous external outcomes or blind retries safe.
Workflow-specific debt includes automatic claiming/leases, generalized durable
command receipts for plan decisions, unknown-outcome reconciliation, arbitrary
workflow APIs, parallel DAGs, compensation, and in-place replanning.
The manifest shape contains no credential configuration, but semantic secret
scanning of arbitrary description or schema text is also deferred.
