# Day 2 — Conversation and Execution Data Model

**Status:** Complete

## Goal

Create the durable domain and persistence model for versioned agents,
conversations, ordered messages, logical turns, and physical execution
attempts.

## Accepted design decisions

1. `Conversation` is the logical aggregate root, but appending a message does
   not require loading its complete history.
2. Messages use a positive, per-conversation sequence allocated under a
   PostgreSQL row lock.
3. A conversation stores the default agent version for future turns, while
   every turn pins the actual immutable version used.
4. A `Turn` represents one logical user request; `TurnAttempt` represents one
   physical processing attempt.
5. Committed messages and historical version references are immutable.
6. UUIDs provide identity and an injected UTC clock provides timestamps;
   neither determines message order.
7. Application workflows depend on repository and unit-of-work ports.
8. SQLAlchemy Core tables and translators remain outside the domain model.

## Built

- typed domain identifiers and validation errors;
- `AgentDefinition` and immutable `AgentVersion`;
- `Conversation`, immutable `Message`, `Turn`, and `TurnAttempt`;
- explicit turn and attempt lifecycle transitions;
- SQLAlchemy Core schema and initial Alembic migration;
- repository and unit-of-work ports and SQLAlchemy adapters;
- transactionally locked message-sequence allocation;
- atomic `StartConversation` use case;
- PostgreSQL-backed unit, migration, rollback, constraint, and concurrency tests;
- ADR 0005 documenting the conversation/history/execution split.

## Verification

- migrations upgrade from base and downgrade/re-upgrade cleanly;
- complete conversation graph survives a new session;
- invalid initial content leaves no partial state;
- concurrent message appends receive distinct ordered sequences;
- duplicate sequence and duplicate turn-per-input constraints are enforced;
- a turn cannot reference an input message from another conversation;
- architecture dependency tests, Ruff, mypy, pytest, CI, and container build pass.

## Known exclusions

- HTTP conversation commands;
- execution events and SSE;
- transactional outbox and worker dispatch;
- model provider and LangGraph integration;
- tool registry, policy, and confirmation.

## Commit

`feat(conversations): add durable conversation and turn model`

## Earn

Switchboard separates immutable user-visible history from durable execution.
A turn is one logical request, while attempts preserve physical retry history.
PostgreSQL serializes per-conversation message allocation and independently
enforces the most important cross-record invariants.
