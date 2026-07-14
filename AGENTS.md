# Switchboard Codex Working Agreement

## Mission

Switchboard is a shared AI conversation and agent platform. It provides durable
conversation state, execution events, reconnectable streaming, tool registration,
routing, approval policies, observability, evaluation, and controlled rollout.

This is both a portfolio project and a structured learning course. Preserve the
learning sequence and architectural decisions instead of merely producing code
that passes tests.

## Sources of truth

Before changing code, read:

1. `PROGRESS.md`
2. The current `planning/DAY_NN.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DOMAIN_MODEL.md`
5. Relevant ADRs under `docs/adr/`
6. `SWITCHBOARD_COURSE.md`

`PROGRESS.md` determines the current day and completed work.

The detailed `planning/DAY_NN.md` overrides a simplified description in
`SWITCHBOARD_COURSE.md` when the two differ.

## Delivery workflow

Work on exactly one checkpoint at a time.

Before editing:

1. Inspect `git status --short` and the current diff.
2. Read the checkpoint definition.
3. Inspect the existing implementation and tests.
4. Report any mismatch between documentation and code.
5. State the implementation plan.

During implementation:

- Do not begin the next checkpoint.
- Do not widen scope without explaining why.
- Do not perform unrelated cleanup or broad refactoring.
- Preserve backward compatibility unless the checkpoint explicitly changes it.
- Add or update tests with every behavioral change.
- Prefer the smallest design that satisfies the documented invariants.

After implementation:

1. Run focused tests.
2. Run Ruff.
3. Run strict mypy.
4. Run the full test suite when practical.
5. Report files changed, decisions, test results, and remaining debt.
6. Stop for review before starting another checkpoint.

Do not commit, push, merge, or rewrite Git history unless explicitly instructed.

## Architecture rules

- Use a modular monolith with separate API and worker processes.
- Keep the domain independent from FastAPI, SQLAlchemy, Redis, LangGraph, and
  provider SDKs.
- Keep application workflows dependent on ports, not concrete adapters.
- PostgreSQL is the durable source of truth.
- Redis may optimize notification or caching but must not be required for
  correctness.
- Use explicit unit-of-work commits.
- Keep polling transactions short.
- Use immutable domain entities and strongly typed identifiers.
- Persist stable platform events, not provider-specific token objects.
- Never persist or expose private model reasoning.
- HTTP/SSE disconnect is not execution cancellation.
- Do not start durable work with an untracked in-process background task.
- Do not claim exactly-once execution across external systems.
- Use database constraints for invariants that can be expressed relationally.
- Use focused updates to avoid overwriting concurrently managed counters.

## Python standards

- Python 3.13.
- Strict mypy.
- Ruff formatting and linting.
- Pytest and pytest-asyncio.
- Time values must be timezone-aware and normalized to UTC.
- Prefer frozen dataclasses and explicit domain transitions.
- Prefer `StrEnum` for persisted lifecycle and event values.
- Avoid `Any` unless an external boundary makes it unavoidable.
- Do not silence type errors with broad casts when the API can be modeled
  correctly.

## Required validation

Run after meaningful changes:

```powershell
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest