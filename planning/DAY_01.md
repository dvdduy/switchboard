# Day 1 — Repository Scaffold and Architecture Skeleton

**Status:** Complete

## Goal

Create a clean, runnable repository that makes the intended module boundaries visible before adding product behavior.

## Learn

- Why a modular monolith is the correct starting architecture.
- Difference between API request lifecycle and durable turn execution.
- Dependency direction in a ports-and-adapters design.
- Why frameworks should stay outside the domain model.
- What belongs in the first CI gate.

## Design decisions to confirm

1. Python version: recommended `3.13` if all dependencies support it; otherwise `3.12`.
2. Packaging/dependencies: recommended `uv` with `pyproject.toml`.
3. Persistence: SQLAlchemy 2 + Alembic + PostgreSQL.
4. API: FastAPI with Pydantic v2.
5. Tests and quality: pytest, mypy, ruff, and a formatter policy.
6. Model development mode: deterministic fake provider first; optional real provider adapter later.
7. Async stance: choose deliberately and keep it consistent across API and persistence.

## Build

- initialize repository metadata and `pyproject.toml`;
- create package/module skeleton matching `docs/ARCHITECTURE.md`;
- create API and worker entry points;
- create Docker Compose with PostgreSQL and Redis;
- add health/readiness endpoints;
- add configuration loading and startup validation;
- add initial CI workflow;
- add `make` or task-runner commands for setup, check, test, and local run;
- add one architecture-boundary test or import rule if practical.

## Tests

- API health endpoint returns success.
- Worker process starts and shuts down cleanly.
- PostgreSQL and Redis connectivity checks work.
- Configuration rejects missing required values.
- `check` runs lint, type checking, and tests.
- Docker Compose smoke test starts the stack.

## Definition of done

- clean clone can run the documented setup;
- API and worker have distinct entry points;
- no domain package imports FastAPI, SQLAlchemy, Redis, LangGraph, or provider SDKs;
- local CI-equivalent command is green;
- `PROGRESS.md` is updated;
- architecture deviations are documented.

## Suggested commit

```text
chore(scaffold): establish Switchboard API, worker, and local infrastructure
```

## Earn

You can explain why Switchboard starts as a modular monolith with a separate durable worker, and how dependency direction protects the platform from framework coupling.


## Completion summary

The repository now has Python 3.13 and `uv`, inward dependency rules, FastAPI
health/readiness endpoints, independently runnable API and worker processes,
PostgreSQL and Redis infrastructure, Docker packaging, and a CI quality gate.

**Commit:** `chore(scaffold): establish Switchboard API, worker, and local infrastructure`
