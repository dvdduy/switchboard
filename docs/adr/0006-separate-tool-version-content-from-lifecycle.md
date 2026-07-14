# ADR 0006 — Separate Tool Version Content from Lifecycle State

**Status:** Accepted

## Context

Tool manifests must remain reproducible after publication, while operators still
need to activate, deprecate, or disable an exact version. Rewriting a manifest
row to change availability would mix immutable execution evidence with mutable
control-plane state and make concurrent lifecycle changes difficult to detect.

Activation also needs durable evidence that a successful conformance run tested
the exact version being activated. Agent versions are already immutable, so
adding a tool binding must not modify an existing agent version.

## Decision

- `ToolDefinition` is the stable team-owned identity.
- `ToolVersion` stores immutable validated manifest content and its canonical
  content hash.
- `ToolVersionState` separately stores lifecycle status, revision, timestamps,
  and the exact conformance run authorizing activation.
- Lifecycle writes use revision compare-and-set semantics.
- `DISABLED` is terminal; reactivation requires publishing a new version.
- An `AgentToolBinding` references an exact tool version. Adding a binding clones
  the base agent version and its existing bindings into a newly sequenced agent
  version.
- Eligibility is derived from current active state, an exact binding, matching
  team ownership, and successful activation conformance. Runtime authorization
  and health remain later filters.

## Alternatives considered

### Store lifecycle status on `ToolVersion`

Rejected because mutable availability would share a record with immutable
manifest evidence and encourage broad updates to historical content.

### Rewrite an existing agent version when bindings change

Rejected because historical conversations and evaluations would no longer pin
reproducible behavior.

### Treat successful schema validation as activation evidence

Rejected because JSON Schema proves data shape, not timeout behavior, normalized
errors, idempotency propagation, reconciliation, or redaction.

## Consequences

- The schema has additional lifecycle, conformance, and binding records.
- Activation and binding require explicit transactional workflows.
- Concurrent lifecycle changes can fail cleanly instead of silently overwriting
  one another.
- Historical manifests and agent versions remain reproducible.
- Eligibility can change when a version is deprecated or disabled without
  mutating historical bindings.
- Production dispatch, authorization, live health, and unknown-outcome recovery
  remain separate future concerns.
