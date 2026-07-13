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
- Switchboard API authentication
- policy engine
- schema validation
- durable approval records
- tool executor
- audit persistence
```

## Authentication and authorization

The first implementation may use development identities, but the domain contract must model:

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

Input context includes:

- actor and team;
- agent version;
- tool version and effect;
- required and granted scopes;
- environment;
- normalized argument summary;
- data sensitivity;
- prior approval;
- configured budgets and quotas.

Output is structured and versioned:

```text
ALLOW
DENY
REQUIRE_CONFIRMATION
REQUIRE_ELEVATED_APPROVAL
```

## Approval integrity

An approval request stores:

- human-readable action summary;
- tool version;
- normalized-argument fingerprint;
- policy version;
- required approver class;
- expiration;
- requester and approver identity.

Any meaningful argument or policy change invalidates the approval.

## Prompt-injection and untrusted output

Tool output and retrieved documents are data, not authority. They cannot:

- grant scopes;
- change tool effect classification;
- override platform instructions;
- disable confirmation;
- alter policy decisions;
- cause a new tool to become bound.

The reference tool suite includes malicious-looking content to verify these boundaries.

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
