# Switchboard

Switchboard is shared backend infrastructure for AI chat and agent products.

It provides:

- versioned conversations and agents;
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

---

## 6. Run the automated checks

```powershell
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest
```