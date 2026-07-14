# Switchboard

Switchboard is shared backend infrastructure for AI chat and agent products.

The current implementation provides:

- versioned conversations and agents;
- durable logical turns and physical attempts;
- immutable PostgreSQL execution events with deterministic per-turn ordering;
- deterministic simulated assistant execution;
- reconnectable read-only SSE replay and tailing.

Planned capabilities include:

- tool registration and routing;
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

## Quality gate

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
docker build --tag switchboard:local .
```

Day 3 intentionally does not include a transactional outbox, durable worker
claiming/recovery, a real model provider, Redis event notification, event
retention policy, or production chunk-size tuning.
