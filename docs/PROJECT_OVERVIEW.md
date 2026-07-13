# Project Overview

## Product statement

Switchboard is the backend platform underneath AI chat and agent products. Product teams use it to configure agents, register tools, run durable conversations, execute actions safely, measure quality, debug failures, and release changes gradually without rebuilding this infrastructure for each product surface.

Switchboard is not a standalone end-user chat product. A web chat, Slack assistant, mobile assistant, or project-management copilot is a client of Switchboard.

## Problem

Independent product teams commonly rebuild the same infrastructure:

- conversation persistence and context management;
- model and tool orchestration;
- tool discovery and selection;
- confirmation and authorization;
- retries, idempotency, and partial-failure recovery;
- streaming;
- tracing and incident reconstruction;
- agent evaluation;
- canary rollout and rollback.

That duplication leads to inconsistent safety, weak reliability guarantees, repeated operational work, and poor cross-team leverage.

## Value proposition

A product team should be able to:

1. define and version an agent;
2. register or bind versioned tools;
3. call a stable conversation API;
4. inherit routing, policies, durable execution, tracing, evaluation, and rollout controls;
5. ship without modifying Switchboard internals.

## Primary actors

### End user
Interacts with a product-specific chat experience and expects correct, safe, responsive behavior.

### Product developer
Configures agents, selects tools and policies, integrates the conversation API, and supplies evaluation cases.

### Tool developer
Publishes versioned tool manifests and implementations with schemas, effect classification, authorization scopes, timeout behavior, idempotency capabilities, and ownership metadata.

### Platform engineer
Owns shared APIs, durable execution, routing, policy enforcement, evaluation, observability, release safety, and platform reliability.

### Reviewer or operator
Investigates traces, reviews uncertain outcomes, approves releases, and responds to incidents.

## Product principles

1. **Safe uncertainty beats confident guessing.** Ambiguous and unsupported requests produce clarification or fallback.
2. **Mutations are explicit.** Read-only behavior is the default; mutating and privileged actions require policy approval and usually user confirmation.
3. **Behavior is versioned.** Conversations and eval runs pin the exact agent, prompt, model, router, policy, and tool versions used.
4. **Durability precedes convenience.** Critical conversation and execution state survives process and worker failure.
5. **A timeout is not proof of failure.** External side effects can enter an unknown-outcome state requiring reconciliation.
6. **Evaluation is part of delivery.** Offline evaluation blocks regressions before release, while live metrics protect staged rollouts.
7. **Debugging is designed in.** Structured execution events reconstruct decisions and outcomes without depending on raw model reasoning.
8. **Platform boundaries stay stable.** Frameworks such as LangGraph are implementation details, not public contracts.

## In scope for the first capstone

- versioned agents, tools, and policies;
- versioned conversation API;
- durable multi-turn state;
- SSE response and event streaming;
- hybrid semantic tool routing with explicit fallback;
- policy and confirmation gates;
- idempotent tool invocation and unknown-outcome reconciliation;
- execution traces and safe replay of recorded history;
- versioned golden datasets and CI evaluation gates;
- shadow and canary rollout with automated rollback simulation;
- Docker Compose local environment and GitHub Actions CI.

## Non-goals for the first capstone

- training a new foundation model;
- a general-purpose ML training platform;
- production multi-region active-active deployment;
- a complete user-facing chat application;
- production-scale routing across many model providers;
- unrestricted arbitrary code tools;
- a full enterprise identity product;
- claiming exactly-once execution across external systems;
- storing or exposing private chain-of-thought reasoning.

## Success criteria

The final demo must prove that:

1. a product team can add a tool without changing routing-platform code;
2. an ambiguous request is clarified instead of incorrectly executed;
3. a mutation cannot execute without a valid policy decision and approval;
4. a worker crash does not duplicate an externally visible effect;
5. an unknown external outcome is reconciled safely;
6. a trace explains routing, policy, execution, latency, and version context;
7. a bad agent/router change is blocked by offline evaluation;
8. a subtler regression is detected during canary and rolled back;
9. all behavior runs locally through documented commands.
