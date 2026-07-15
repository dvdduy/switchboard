# Security and Policy Model

## Objectives

- Prevent unauthorized or accidental tool execution.
- Make mutations explicit and auditable.
- Isolate teams and agents.
- Treat model and tool content as untrusted.
- Avoid leaking sensitive conversation or tool data through telemetry and evaluation.

## Trust boundaries

```text
Untrusted / partially trusted:
- end-user text
- retrieved documents
- tool output
- model output
- external tool endpoints
- product-provided descriptions and prompts

Trusted enforcement boundary:
- trusted Phase 1 development team/actor context
- policy engine
- schema validation
- durable approval records
- tool executor
- audit persistence
```

## Authentication and authorization

Phase 1 uses explicit `X-Team-ID` and `X-Actor-ID` UUIDs as trusted development
fixtures. They are not authentication, membership proof, or delegated authority.
The domain contract nevertheless models:

- authenticated actor ID;
- team membership;
- delegated tool scopes;
- agent permissions;
- operator roles;
- approval authority.

Authorization is checked before routing where possible and again before tool dispatch to avoid time-of-check/time-of-use gaps.

## Tool effect classes

- `READ_ONLY`: no externally visible mutation.
- `MUTATING`: changes product data and normally requires confirmation.
- `EXTERNAL_SIDE_EFFECT`: sends messages, creates irreversible work, charges money, or affects systems outside the product boundary; stricter policy and reconciliation required.
- `PRIVILEGED`: administrative or security-sensitive operation requiring elevated approval.

A tool's class is part of its immutable manifest version.

## Policy evaluation

The implemented pure `day8-v1` evaluator receives:

- actor and team;
- agent version;
- tool version and effect;
- required and granted scopes;
- environment;
- canonical immutable arguments.

Output is structured and versioned:

```text
ALLOW
DENY
REQUIRE_CONFIRMATION
REQUIRE_ELEVATED_APPROVAL
```

Valid read-only calls return `ALLOW`; valid mutating calls return
`REQUIRE_CONFIRMATION`; external-side-effect and privileged calls return
`DENY`. Ownership, binding, activation/conformance, and scope failures take
precedence. Elevated approval remains reserved.

## Approval integrity

The implemented approval request stores:

- exact tool identities and a value-free summary of effect and sorted top-level
  argument field names;
- an `action-v1` SHA-256 fingerprint covering team, requester, agent/tool
  versions, effect, environment, policy version, and canonical arguments;
- expiration;
- requester and approver identity.

The linked invocation remains the durable argument source. Public reads/events
omit argument values and the digest. Any meaningful action or policy change
invalidates resume.

Decision commands fingerprint team, approval, actor, and decision behind a
hashed idempotency key. PostgreSQL row locking and compare-and-set lifecycle
updates select one decision/resume winner. Approval consumption, invocation
start, resumed lifecycle, and `tool.started` commit before adapter dispatch;
the adapter call holds no transaction.

Phase 1 adds one separate `WorkflowPlanApproval` for a frozen ordered mutation
plan. Its `workflow-plan-v1` fingerprint binds team, requester, agent version,
workflow and plan version, environment, policy version, and every ordered
mutation's step number, invocation identity, and exact `action-v1` fingerprint.
The public contract exposes only counts and value-free safe action summaries.
Discovery output is untrusted data: a bounded platform template, schema
validation, exact tool ownership/binding/state, scopes, and policy—not model or
tool text—determine which mutations may enter the plan. No action may be added
or changed after freeze or approval.

Workflow approval decisions are stable lifecycle replays, but generalized
command-receipt enforcement for cross-approval idempotency-key reuse remains
deferred. This is distinct from the durable Day 8 single-action receipt
guarantee.

## Prompt-injection and untrusted output

Tool output and retrieved documents are data, not authority. They cannot:

- grant scopes;
- change tool effect classification;
- override platform instructions;
- disable confirmation;
- alter policy decisions;
- cause a new tool to become bound.
- expand, reorder, or rewrite a frozen workflow plan.

Focused tests use malicious-looking argument text to verify these boundaries;
it affects the fingerprint as data but never grants authority.

Day 10 rechecks these boundaries through the public demo and failure matrix:
approval summaries remain value-free, tool events omit arguments and results,
malformed model actions fail safely, disabled tools never dispatch, changed
approved arguments cannot resume, and unknown mutation outcomes stop without a
blind retry. The deterministic environment uses synthetic data and no provider
credentials.

## Data handling

Data is classified at least as:

- public/test;
- internal;
- confidential;
- sensitive/restricted.

Requirements:

- redact configured fields in logs and traces;
- avoid placing secrets in prompts or eval exports;
- separate raw payload access from ordinary operator views;
- record access to privileged traces;
- define retention for messages, arguments, results, and eval artifacts;
- use synthetic data in the public portfolio repository.

## Audit events

Mutating and privileged actions record:

- requester;
- approver when applicable;
- agent, policy, and tool versions;
- safe argument summary/fingerprint;
- idempotency key reference;
- dispatch and final outcome;
- external operation reference when available.

## Threat scenarios to test

1. User asks the model to ignore confirmation policy.
2. Retrieved document says to call a privileged tool.
3. Tool returns text instructing the model to exfiltrate secrets.
4. Approval is replayed with changed arguments.
5. Tool version is disabled after routing but before dispatch.
6. Cross-team conversation or trace ID is supplied.
7. Sensitive tool output is accidentally included in logs.
8. Model returns malformed or unexpected tool arguments.
9. Rate-limited user attempts to bypass quotas through concurrent turns.
10. Operator tries to “replay” a mutation and unintentionally executes it again.

## Deferred production concerns

- enterprise SSO and SCIM;
- fine-grained ABAC administration UI;
- customer-managed encryption keys;
- regional data residency;
- formal compliance certification;
- production secret rotation and vault integration.
- separation of duties, elevated/admin approval, and approval delegation;
- external-side-effect execution, unknown-outcome reconciliation, and safe
  post-dispatch crash recovery;
- approval notification, batching, retention, and richer sensitivity-aware
  summaries.
