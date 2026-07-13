# ADR-0001: Start with a Modular Monolith and Separate Worker

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

Switchboard contains conversation APIs, agent/tool configuration, durable execution, policies, evaluation, tracing, and releases. Splitting these into many deployables would add networking, deployment, and data-consistency work before the product boundaries are proven.

At the same time, durable turn execution should not run inside request handlers and must survive API-process failure.

## Decision

Use one repository and shared application/domain packages with two primary processes:

1. FastAPI control/conversation API;
2. background execution worker.

PostgreSQL is the durable source of truth. Modules preserve control-plane/data-plane boundaries in code. Docker Compose runs API, worker, PostgreSQL, Redis, and eval jobs.

## Alternatives considered

### Single API process with background tasks

Rejected because process-local tasks can be lost on restart and blur request and execution lifecycles.

### Microservices from Day 1

Rejected because service boundaries and scale requirements are not yet validated. It would increase operational work without improving the core learning outcomes.

### Serverless functions

Deferred. Long-running, resumable workflows and local-first development make the worker model easier to reason about initially.

## Consequences

- Easier transactions, local development, refactoring, and integration testing.
- API and worker scale independently.
- Module discipline is required to avoid a tightly coupled “big ball of mud.”
- Future extraction remains possible through stable ports and ownership boundaries.
