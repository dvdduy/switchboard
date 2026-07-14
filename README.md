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
- deterministic read-only and idempotent mutating reference adapters.

Planned capabilities include:

- semantic tool routing and runtime invocation;
- safe and durable tool execution;
- policy enforcement;
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

The public API currently exposes health/readiness and the read-only event stream.
It does not yet expose a command that creates or continues conversations.

Given an existing turn ID created through the application/persistence workflow,
observe its committed events:

```bash
curl -N \
  -H "Accept: text/event-stream" \
  http://127.0.0.1:8000/api/v1/turns/<turn-id>/events
```

Reconnect after sequence 3 using the exclusive cursor:

```bash
curl -N \
  -H "Accept: text/event-stream" \
  -H "Last-Event-ID: 3" \
  http://127.0.0.1:8000/api/v1/turns/<turn-id>/events
```

Frames use the durable turn-local sequence as `id`, a stable platform event name
as `event`, and a compact JSON payload as `data`. Disconnecting the observer does
not cancel or mutate the turn.

## Context management

Each immutable agent version declares its model-window budget, reserved output,
fixed instruction/tool overhead, maximum summary size, and minimum recent
message count. Context reconstruction reads only through the selected turn's
input message, preserves the current input and configured recent floor, and
uses a durable provenance-bearing summary for an omitted older prefix.

The counter and summarizer boundaries are provider-independent. The included
summarizer is deterministic and extractive for development and tests; it is not
a production model tokenizer or semantic-memory system. Context reconstruction
is currently an application workflow and is not exposed as a public endpoint or
connected to a real model loop.

## Tool registry

Day 5 adds an application-level control plane for registering team-owned tool
definitions, publishing validated immutable manifests, running deterministic
conformance, activating exact versions, and cloning agent versions with exact
tool bindings. JSON Schema Draft 2020-12 validation is bounded, remote references
are rejected, and diagnostics do not copy rejected values.

The included `search_work_items` and `update_due_date` adapters are deterministic
local examples. The latter demonstrates stable idempotency keys and
reconciliation, but its mutation state is intentionally in-memory. No public
tool-management endpoint, semantic router, runtime authorization/health filter,
or durable production tool dispatcher exists yet.

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
dispatch and recovery, runtime authorization and health filtering, and
conformance retention/telemetry policy.
The manifest shape contains no credential configuration, but semantic secret
scanning of arbitrary description or schema text is also deferred.
