# Day 5 — Versioned Tool Registry and Conformance

**Status:** Planned
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Day 4 complete

## Goal

Create a control-plane registry where teams can register immutable, versioned
tool manifests, validate their safety and execution contract, run deterministic
conformance checks, activate acceptable versions, and bind active versions to
agents without changing Switchboard routing code.

## Learn

- A tool registry is a platform contract, not a global dictionary of functions.
- Stable tool identity and immutable tool versions allow reproducibility.
- JSON Schema validates shape but does not prove behavioral conformance.
- Effect classification, scopes, timeout, retry behavior, idempotency, and
  reconciliation are part of the tool contract.
- Registration, activation, binding, and runtime execution are separate.
- Dynamic registration must not mean arbitrary code upload.

## Why this matters

This establishes FR-010 through FR-013 and enables later orchestration, policy,
routing, and evaluation. A product team should add a reference tool through a
manifest and adapter contract rather than editing routing internals.

## Current context

Switchboard currently has teams, immutable `AgentVersion` records, bounded
context, and no tool entities, manifests, bindings, conformance history, or
runtime adapter registry.

## Accepted direction

1. Model:
   - `ToolDefinition` as stable team-owned identity;
   - `ToolVersion` as immutable manifest and execution configuration;
   - lifecycle state `DRAFT`, `ACTIVE`, `DEPRECATED`, or `DISABLED`;
   - `ToolConformanceRun` with case-level results;
   - `AgentToolBinding` from one immutable agent version to one tool version.
2. Require manifests to declare:
   - name and description;
   - input/output JSON Schemas;
   - `ToolEffect`;
   - required scopes;
   - timeout and retry policy;
   - idempotency and reconciliation support;
   - owner/team;
   - stable adapter key or endpoint reference;
   - redaction metadata for sensitive fields.
3. Manifests never contain executable Python source, credentials, or secrets.
4. Runtime adapters resolve through an application port keyed by adapter
   reference.
5. Activation requires valid manifest, successful deterministic conformance, and
   a safe effect/idempotency/reconciliation combination.
6. Version content is immutable after creation. Lifecycle changes never rewrite
   historical manifest content.
7. Only active versions may be newly bound. Historical records can still refer
   to deprecated or disabled versions.
8. Binding a tool creates a new agent version or modifies only an unpublished
   draft; published agent versions remain immutable.

## Design questions to resolve

1. Whether lifecycle state is on `ToolVersion` or a separate activation record.
2. Supported JSON Schema draft and validation library.
3. Whether local and HTTP tools share one normalized execution port.
4. How deeply conformance executes external endpoints in local development.
5. Exact binding uniqueness and whether aliases are deferred.

## Build checkpoints

### Checkpoint 0 — Reconcile requirements and security rules

- review FR-010 through FR-014 and UC-01;
- define one read-only and one mutating reference manifest;
- decide schema draft and normalized diagnostics;
- document invalid effect/idempotency/reconciliation combinations.

### Checkpoint 1 — Tool domain and manifest validation

Build typed identifiers, `ToolEffect`, lifecycle enums, retry/idempotency/
reconciliation policies, immutable `ToolManifest`, `ToolDefinition`,
`ToolVersion`, `AgentToolBinding`, lifecycle transitions, and structured
diagnostics with safe path/code/message values.

Validate ownership, nonblank identity, schemas, bounded timeout/retries, scopes,
sensitive fields, and effect/idempotency coherence.

### Checkpoint 2 — Registry persistence and versioning

Add repository ports, SQLAlchemy schema, translators, and migration for
definitions, versions, lifecycle state, bindings, conformance runs, and case
results.

Enforce team ownership, positive unique version numbers, immutable historical
references, binding uniqueness, and indexes for active eligible queries.

### Checkpoint 3 — Conformance framework

Define provider-independent `ToolAdapter`/`ToolInvoker` and
`ToolAdapterResolver` ports.

Run deterministic cases for:

- valid input and normalized output;
- invalid input rejected before invocation;
- invalid output;
- timeout;
- declared error mapping;
- idempotency-key propagation;
- reconciliation declaration;
- sensitive-field redaction.

Persist run and case results. A failed run leaves the version in `DRAFT`.

### Checkpoint 4 — Registration, activation, and binding use cases

Build:

- `RegisterToolDefinition`;
- `PublishToolVersion`;
- `RunToolConformance`;
- `ActivateToolVersion`;
- `DeprecateToolVersion`;
- `DisableToolVersion`;
- `BindToolVersionToAgentVersion`.

Use explicit units of work and compare-and-set lifecycle transitions where
concurrent operations can race.

### Checkpoint 5 — Reference adapters and eligible registry query

Add deterministic reference adapters:

- read-only `search_work_items`;
- mutating `update_due_date` requiring an idempotency key.

Build a query returning active, bound manifests for a pinned agent version. It
must exclude disabled, unbound, cross-team, and conformance-failed tools.

### Checkpoint 6 — Documentation and verification

Update domain, architecture, requirements/use-case evidence, testing strategy,
`PROGRESS.md`, and this plan. Add an ADR if the manifest/version/lifecycle split
is not already sufficiently documented.

## Required tests

### Unit

- manifest normalization and diagnostics;
- version/lifecycle invariants;
- effect/idempotency/retry/reconciliation combinations;
- JSON Schema acceptance/rejection;
- binding rules and redaction.

### Application and tool contract

- failed conformance cannot activate;
- active version can bind;
- disabled/deprecated behavior;
- adapter resolution failure;
- valid/invalid input and output;
- timeout/error mapping;
- idempotency-key propagation;
- reconciliation declaration;
- new tool registration without orchestration code change.

### PostgreSQL integration

- migration round-trip;
- version uniqueness;
- lifecycle races;
- immutable references;
- active-bound query;
- cross-team rejection.

### Architecture

- domain does not import schema libraries, HTTP clients, FastAPI, SQLAlchemy,
  LangGraph, or concrete tool adapters;
- conformance depends on ports.

## Migration impact

Expected tables for definitions, versions, bindings, conformance runs, and case
results. Manifest JSONB is acceptable only after domain validation/freezing.

Credentials are never stored in manifest JSON; future secret references are
separate.

## Security and safety considerations

- Registration is a team-owned control-plane action.
- Descriptions/schemas are untrusted and cannot grant scopes or override policy.
- Mutating/privileged effects cannot default to read-only.
- Secrets are rejected from manifests.
- Sensitive fields require deterministic redaction metadata.
- Conformance is bounded by timeout/resource limits.
- Tool output remains untrusted for future model calls.

## Out of scope

- semantic routing;
- production service discovery;
- arbitrary uploaded code/plugin installation;
- secret management/OAuth;
- live health scoring/automatic disable;
- real external SaaS adapters;
- approval flow;
- unknown-outcome reconciliation;
- public tool-management APIs beyond minimal contract demonstration.

## Definition of done

- [ ] Tool definitions and immutable versions are durable.
- [ ] Manifests include schemas, effect, scopes, timeout, retry, ownership,
      idempotency, reconciliation, and redaction declarations.
- [ ] Invalid manifests return structured diagnostics.
- [ ] Conformance persists case-level results.
- [ ] Failed conformance cannot activate.
- [ ] Active versions can bind to immutable agent versions.
- [ ] Disabled/unbound/cross-team tools are excluded.
- [ ] A new reference tool requires no orchestration/routing code change.
- [ ] Migration and all quality gates pass.
- [ ] Documentation and `PROGRESS.md` match actual behavior.

## Suggested commit

`feat(tools): add versioned registry and conformance gates`

## Earn

You can explain how other teams register tools safely, why schema validation is
not enough, and how immutable manifests plus conformance preserve
reproducibility and operational control.

## Assumptions to revisit

- Production tools may use local, HTTP, MCP, or queue adapters.
- Live health will later affect routing eligibility.
- Bindings may later include aliases, quotas, and policy overrides.
