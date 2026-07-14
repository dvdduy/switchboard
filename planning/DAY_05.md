# Day 5 — Versioned Tool Registry and Conformance

**Status:** Complete
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

Day 5 began from teams, immutable `AgentVersion` records, and bounded context
without tool entities, manifests, bindings, conformance history, or a runtime
adapter registry. The implementation described below is now complete.

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
   - stable local adapter key;
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
8. Binding a tool creates a new agent version; existing agent versions remain
   immutable.

## Resolved Phase 1 contract

### Manifest and schema vocabulary

- `ToolDefinition` is a stable team-owned identity with a unique normalized
  `tool_key` inside the team.
- Team ownership is carried by `ToolDefinition` and inherited by every version;
  it is not a caller-controlled field duplicated inside manifest JSON.
- `ToolVersion` contains immutable manifest content and a deterministic content
  hash. Positive version numbers are unique within one definition.
- Manifests use `switchboard.tool-manifest/v1` and require JSON Schema Draft
  2020-12 for input and output schemas.
- The domain freezes and validates JSON-compatible values without importing a
  schema library. An application port delegates schema-dialect validation to a
  `jsonschema` adapter.
- Phase 1 rejects `$ref`, `$dynamicRef`, and other remote-resolution behavior.
  Reusable schema documents and network retrieval are deferred.
- Schemas, descriptions, scopes, retry declarations, redaction paths, nesting
  depth, and diagnostic counts are bounded:
  - tool and adapter keys: 1–100 characters matching lowercase
    `a-z`, digits, `.`, `_`, and `-`, beginning with a letter;
  - display name: 1–200 characters;
  - description: 1–2,000 characters;
  - each canonical JSON schema: at most 65,536 UTF-8 bytes, depth 20, and 2,000
    object/array nodes;
  - at most 32 required scopes, each 1–100 normalized characters;
  - at most 64 redaction pointers, each at most 512 characters;
  - timeout: 1–30,000 milliseconds;
  - retry attempts: 1–3 with initial backoff 0–5,000 milliseconds;
  - at most 100 returned diagnostics, with one safe truncation diagnostic when
    additional failures exist.
- A manifest accepts only a normalized local `adapter_key`. It does not accept
  Python source, import paths, URLs, headers, credentials, or arbitrary adapter
  configuration.
- Required scopes state prerequisites; a manifest cannot grant scopes.
- Sensitive input/output fields use validated JSON Pointer paths. Values are
  never copied into validation diagnostics or conformance case records.

The runtime dependency added in Checkpoint 1 is `jsonschema`, using
`Draft202012Validator`. The domain and application contracts remain independent
of that concrete library.

### Stable validation diagnostics

Invalid manifests return an ordered tuple of safe diagnostics:

```text
code:    stable machine-readable identifier
path:    tuple of string/integer path segments
message: safe human-readable explanation without the rejected value
```

Initial diagnostic families are:

```text
manifest.identity.*
manifest.schema.unsupported_draft
manifest.schema.invalid
manifest.schema.reference_forbidden
manifest.effect.unsafe_capability
manifest.scope.invalid
manifest.retry.invalid
manifest.redaction.invalid_path
manifest.adapter.invalid_key
manifest.bounds.exceeded
manifest.field.forbidden
```

Diagnostics sort by path and code so repeated validation is deterministic.

### Effect, retry, idempotency, and reconciliation matrix

| Effect | Idempotency | Reconciliation | Retry rule |
|---|---|---|---|
| `READ_ONLY` | Optional | Must be `NONE` | Bounded retries allowed for declared transient errors |
| `MUTATING` | `REQUIRED` | `BY_IDEMPOTENCY_KEY` required | Bounded retries only with the same logical key |
| `EXTERNAL_SIDE_EFFECT` | `REQUIRED` | `BY_IDEMPOTENCY_KEY` required | Bounded retries only after reconciliation establishes safety |
| `PRIVILEGED` | `REQUIRED` | `BY_IDEMPOTENCY_KEY` required | Conservative mutating rules plus later elevated approval |

Phase 1 treats `PRIVILEGED` conservatively because the existing effect taxonomy
does not separately encode privilege and mutability. A non-read-only manifest
without both capabilities is invalid. This declaration does not implement
exactly-once execution; ADR-0002 still governs future dispatch.

### Immutable content and mutable lifecycle

Manifest content remains on immutable `ToolVersion`. A separate
`ToolVersionState` owns lifecycle status, a compare-and-set revision, transition
timestamps, and the conformance run that authorized activation.

Allowed transitions are:

```text
DRAFT -> ACTIVE
DRAFT -> DISABLED
ACTIVE -> DEPRECATED
ACTIVE -> DISABLED
DEPRECATED -> DISABLED
```

`DISABLED` is terminal. Reactivation requires publishing a new version. Multiple
versions of one definition may remain active because agents bind exact versions.
Only `ACTIVE` versions may receive new bindings or appear in eligible queries.

### Agent bindings

The existing `AgentVersion` is already immutable and has no unpublished draft
state. Therefore `BindToolVersionToAgentVersion` creates a new agent version: it
copies the base context policy and existing bindings, adds the selected active
tool version, and allocates the next agent version number under the agent
definition lock. It never mutates the base version.

One agent version may bind at most one version of a stable tool definition.
Aliases, per-binding scopes, quotas, policy overrides, and binding removal are
deferred. Team ownership is checked by the application and supported by
relational identities and uniqueness constraints where practical.

### Adapter and conformance boundary

- `ToolAdapter` exposes normalized invoke and reconciliation operations.
- `ToolAdapterResolver` maps a manifest `adapter_key` to a preinstalled adapter.
  The bootstrap mapping is immutable after startup.
- Local deterministic adapters are the only Day 5 implementation. HTTP, MCP,
  queue, and dynamically uploaded adapters remain future implementations of the
  same ports.
- Platform-owned synthetic conformance cases validate inputs before invocation,
  validate normalized outputs, enforce timeout, inspect declared errors, prove
  idempotency-key propagation, exercise reconciliation when required, and check
  redaction.
- Adapter calls run without an open database transaction. A complete immutable
  run and its case results are persisted together afterward. Cancellation before
  that write leaves no partial run.
- Activation names a committed successful conformance run for the exact tool
  version and uses compare-and-set lifecycle transition semantics.

### Reference manifests

The read-only reference definition is `search_work_items`. Its version 1
manifest uses adapter `reference.search_work_items.v1`, scope
`work_items:read`, a bounded retry policy, no idempotency requirement, and no
reconciliation. Its input requires a nonblank query and optional result limit;
its output is an object containing an array of normalized work-item summaries.

The mutating reference definition is `update_due_date`. Its version 1 manifest
uses adapter `reference.update_due_date.v1`, scopes `work_items:read` and
`work_items:write`, required idempotency, reconciliation by idempotency key, and
bounded retries. Its input requires a work-item ID and ISO date; its output
contains the updated ID/date and an operation reference. The operation-reference
path is marked sensitive for deterministic redaction tests. Approval and actual
conversation-time execution remain out of scope.

Both manifests:

- declare `https://json-schema.org/draft/2020-12/schema`;
- use object roots with `additionalProperties: false`;
- contain synthetic metadata only;
- contain no secret values, executable source, endpoint configuration, or
  authority-granting instructions.

### Requirement boundary

Day 5 completes FR-010 through FR-013. It establishes the binding and lifecycle
eligibility portion of FR-014, but does not claim runtime authorization or health
filtering. The future router filters an eligible registry result further by
actor scopes and health, and the future executor must recheck lifecycle and
authorization immediately before dispatch.

No public HTTP tool-management endpoint is added on Day 5. The versioned
manifest, application commands/results, diagnostics, adapter ports, lifecycle
values, and eligible-query result are the contracts demonstrated by tests.

## Build checkpoints

### Checkpoint 0 — Reconcile requirements and security rules

- [x] review FR-010 through FR-014, UC-01, ADR-0002, ADR-0003, and security rules;
- [x] define one read-only and one mutating reference manifest;
- [x] decide schema draft, validation boundary, and normalized diagnostics;
- [x] document invalid effect/idempotency/reconciliation combinations;
- [x] resolve lifecycle placement, immutable agent binding, adapter scope, and
  FR-014 boundary.

### Checkpoint 1 — Tool domain and manifest validation

- [x] Build typed identifiers, `ToolEffect`, lifecycle enums, retry/idempotency/
reconciliation policies, immutable `ToolManifest`, `ToolDefinition`,
`ToolVersion`, `ToolVersionState`, `AgentToolBinding`, lifecycle transitions,
and structured diagnostics with safe path/code/message values.

- [x] Validate ownership, nonblank identity, schemas, bounded timeout/retries, scopes,
sensitive fields, and effect/idempotency coherence.

### Checkpoint 2 — Registry persistence and versioning

- [x] Add repository ports, SQLAlchemy schema, translators, and migration for
definitions, versions, separate lifecycle state, bindings, conformance runs,
and case results.

- [x] Enforce team ownership, positive unique version numbers, immutable historical
references, binding uniqueness, and indexes for active eligible queries.

### Checkpoint 3 — Conformance framework

- [x] Define provider-independent `ToolAdapter` and `ToolAdapterResolver` ports. The
adapter owns normalized invoke and reconciliation operations; no second
overlapping invoker abstraction is introduced.

- [x] Run deterministic cases for:

- valid input and normalized output;
- invalid input rejected before invocation;
- invalid output;
- timeout;
- declared error mapping;
- idempotency-key propagation;
- reconciliation declaration;
- sensitive-field redaction.

- [x] Persist run and case results. A failed run leaves the version in `DRAFT`.

### Checkpoint 4 — Registration, activation, and binding use cases

- [x] Build:

- `RegisterToolDefinition`;
- `PublishToolVersion`;
- `RunToolConformance`;
- `ActivateToolVersion`;
- `DeprecateToolVersion`;
- `DisableToolVersion`;
- `BindToolVersionToAgentVersion`.

- [x] Use explicit units of work and compare-and-set lifecycle transitions where
concurrent operations can race. Binding returns a newly cloned immutable agent
version rather than changing the base version.

### Checkpoint 5 — Reference adapters and eligible registry query

- [x] Add deterministic reference adapters:

- read-only `search_work_items`;
- mutating `update_due_date` requiring an idempotency key.

- [x] Build a query returning active, bound manifests for a pinned agent version. It
must exclude disabled, unbound, cross-team, and conformance-failed tools.
Authorization and live-health filtering remain explicitly deferred.

### Checkpoint 6 — Documentation and verification

- [x] Update domain, architecture, requirements/use-case evidence, testing strategy,
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

The migration adds tables for definitions, immutable versions, separate
lifecycle state, bindings, conformance runs, and case results. Manifest JSONB is
written only after domain validation/freezing.

The manifest contract has no credential, header, or endpoint-configuration
fields. Callers must not place secrets in descriptions or schema annotations;
semantic secret scanning is not implemented, and future secret references must
remain separate.

## Security and safety considerations

- Registration is a team-owned control-plane action.
- Descriptions/schemas are untrusted and cannot grant scopes or override policy.
- Mutating/privileged effects cannot default to read-only.
- The manifest shape provides no credential fields; semantic secret scanning of
  arbitrary text is deferred, so ingestion callers must provide sanitized text.
- Sensitive fields require deterministic redaction metadata.
- Conformance is bounded by timeout/resource limits.
- Tool output remains untrusted for future model calls.

## Out of scope

- semantic routing;
- runtime authorization and live health filtering;
- production service discovery;
- arbitrary uploaded code/plugin installation;
- secret management/OAuth;
- semantic secret scanning or DLP for manifest descriptions/schema annotations;
- live health scoring/automatic disable;
- real external SaaS adapters;
- approval flow;
- unknown-outcome reconciliation;
- public tool-management APIs beyond minimal contract demonstration;
- remote JSON Schema references and shared schema retrieval;
- tool aliases and per-binding policy, scope, quota, or configuration overrides.

## Definition of done

- [x] Tool definitions and immutable versions are durable.
- [x] Manifests include schemas, effect, scopes, timeout, retry, idempotency,
      reconciliation, and redaction declarations; ownership is inherited from
      the team-owned definition.
- [x] Invalid manifests return structured diagnostics.
- [x] Conformance persists case-level results.
- [x] Failed conformance cannot activate.
- [x] Active versions can bind to immutable agent versions.
- [x] Disabled/unbound/cross-team tools are excluded.
- [x] A new reference tool requires no orchestration/routing code change.
- [x] Migration and all quality gates pass.
- [x] Documentation and `PROGRESS.md` match actual behavior.

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
