# Day 4 — Token-Budgeted Context Window Management

**Status:** Planned
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Days 2–3 complete

## Goal

Build a deterministic, provider-independent context assembly capability that
keeps model input within a declared token budget, preserves the current user
request and designated critical facts, summarizes an older conversation prefix
instead of silently dropping it, and records enough provenance to explain what
the model saw.

## Learn

- A context window is a constrained product and reliability boundary, not a
  string-concatenation helper.
- Token budgets must reserve capacity for system instructions, tool schemas, and
  model output.
- Truncation, rolling summary, retrieval, and semantic memory solve different
  problems.
- Summaries are lossy derived artifacts and need coverage/provenance.
- Provider tokenizers belong behind ports; the application policy must remain
  provider-independent.
- A platform should fail explicitly when mandatory context cannot fit rather
  than quietly discard the active request.

## Why this matters

This implements FR-005 and establishes the state-management capability needed by
the later agent loop. It also prevents a common production failure: a long
conversation silently losing earlier constraints or exceeding a provider limit.

## Current context

Switchboard currently has:

- immutable, ordered conversation messages;
- turns pinned to an immutable `AgentVersion`;
- durable execution events and reconnectable SSE;
- no prompt/model/context configuration on `AgentVersion`;
- no token-counting or summarization contract;
- no durable summary artifacts.

## Accepted direction

1. Introduce an immutable `ContextPolicy` owned by `AgentVersion`.
2. Separate the total model window from:
   - reserved output tokens;
   - fixed instruction/tool overhead;
   - available conversation-input tokens.
3. Use a `TokenCounter` port; deterministic tests use a fake counter.
4. Use a `ConversationSummarizer` port; the first implementation is
   deterministic and local, not a paid provider call.
5. Persist a `ConversationSummary` as a derived artifact with:
   - conversation and agent-version identity;
   - covered message sequence range;
   - summary text;
   - estimated token count;
   - policy/version provenance;
   - creation timestamp.
6. Build context as:
   - fixed instructions/budget metadata;
   - latest valid summary for an older prefix, when needed;
   - the newest contiguous message suffix that fits;
   - the current input message as mandatory context.
7. Do not rewrite conversation history or insert a summary as a user-visible
   `Message`.
8. If mandatory items cannot fit, raise a structured
   `ContextBudgetExceededError`; never silently omit them.
9. Context assembly is deterministic for pinned messages, policy, counter, and
   summary artifact.

The exact tokenizer and summarizer adapters are replaceable. The domain and
application contracts must not import a model-provider SDK.

## Design questions to resolve during implementation

1. Whether `ContextPolicy` is stored as typed relational columns or a validated
   JSON object on `AgentVersion`.
2. Whether summary creation happens eagerly at a threshold or lazily when a
   build first exceeds the budget.
3. Which facts are marked mandatory in Phase 1:
   - current input is always mandatory;
   - a conservative recent-message floor is recommended;
   - richer semantic importance is deferred.
4. Whether one summary may incrementally summarize a prior summary or whether
   each artifact must reference only source messages. The first implementation
   should favor explainability over optimization.

## Build checkpoints

### Checkpoint 0 — Reconcile Day 3 and define the budget contract

- verify Day 3 is committed and the working tree is understood;
- inspect current `AgentVersion`, message repositories, and API DTOs;
- write examples for short, near-limit, over-limit, and impossible contexts;
- decide the initial token-budget vocabulary and summary provenance;
- update the plan before code if the accepted direction changes.

### Checkpoint 1 — Context policy and token-counting domain contract

Build:

- immutable `ContextPolicy`;
- validated positive token-budget fields;
- derived available-input budget calculation;
- `TokenCounter` application port;
- context item/result value objects with source metadata;
- `ContextBudgetExceededError`;
- unit tests for invalid budgets, reserved capacity, and deterministic counting.

The policy must make impossible configurations invalid, such as reserved output
plus fixed overhead consuming the entire model window.

### Checkpoint 2 — Agent-version and summary persistence

Add:

- context policy to immutable `AgentVersion`;
- `ConversationSummary` domain entity and typed identifier;
- summary repository port and SQLAlchemy adapter;
- relational constraints proving:
  - positive sequence coverage;
  - `through_sequence >= from_sequence`;
  - summary ownership by one conversation;
  - nonblank content;
  - positive estimated token count;
- migration with safe defaults/backfill for existing agent versions;
- translators and migration round-trip tests.

Historical turns must continue resolving their pinned agent version and context
policy.

### Checkpoint 3 — Deterministic context assembler

Build a framework-independent service that:

1. loads ordered messages;
2. computes the available conversation budget;
3. includes the newest contiguous suffix that fits;
4. identifies the omitted older prefix;
5. uses a supplied summary for exactly that covered prefix;
6. produces ordered context items and token accounting;
7. never exceeds the budget;
8. never omits the current input;
9. exposes provenance showing summary coverage and included message sequences.

Add property-style tests proving the output never exceeds the budget across
varied histories.

### Checkpoint 4 — Summary creation and build-context use case

Build an application workflow that:

- loads the pinned `AgentVersion`;
- reuses a valid existing summary when possible;
- creates a new deterministic summary when an older prefix requires it;
- persists the summary before returning context;
- handles concurrent attempts to summarize the same coverage without duplicate
  authoritative artifacts;
- returns a complete `BuiltContext` result for the later orchestrator.

Use separate short transactions around durable summary writes. Do not hold a
database transaction open during summarization.

### Checkpoint 5 — Integration, diagnostics, and documentation

Prove with PostgreSQL tests that:

- a long conversation produces a persisted summary plus recent suffix;
- critical/current content survives;
- repeated context builds reuse the same valid summary;
- changed history beyond the covered sequence does not invalidate the prefix;
- a changed agent context policy does not silently reuse an incompatible
  summary;
- rollback leaves no partial summary;
- context output remains under budget after a new session.

Update architecture, domain model, requirements evidence, `PROGRESS.md`, and this
plan with actual implementation decisions.

## Required tests

### Unit

- policy validation and available-budget math;
- token counter and deterministic summarizer contracts;
- newest-suffix selection;
- summary coverage validation;
- impossible mandatory-context failure;
- context result provenance;
- output never exceeds budget.

### Application

- summary reuse versus creation;
- pinned agent-version policy selection;
- no transaction held while summarizing;
- explicit handling of missing conversation/agent version.

### PostgreSQL integration

- migration upgrade/downgrade;
- summary persistence and ownership;
- concurrency for identical summary coverage;
- complete context reconstruction after reopening a session.

### Architecture

- domain does not import provider SDKs, SQLAlchemy, FastAPI, Redis, or LangGraph;
- application depends on token/summarizer ports, not adapters.

## Migration impact

Expected:

- add immutable context-policy configuration to `agent_versions`;
- add `conversation_summaries`;
- add indexes/constraints for conversation coverage and policy provenance.

Existing agent versions require an explicit safe default policy during
migration. Remove temporary database defaults after backfill if the application
must always supply the value.

## Security and safety considerations

- Summaries may contain sensitive conversation content and inherit the same
  tenant/retention protections as messages.
- Do not include secrets or hidden model reasoning.
- Summarizer output is untrusted derived text and must not grant permissions or
  alter policy.
- Logs should record identifiers, coverage, and token counts—not raw sensitive
  content.
- Cross-team summary access must be rejected.

## Out of scope

- provider-specific production tokenizers;
- paid or nondeterministic LLM summarization;
- vector retrieval or long-term semantic memory;
- cross-conversation memory;
- user-editable memories;
- prompt/model configuration beyond the context policy required today;
- background summary compaction;
- summary retention and deletion policy;
- performance optimization for very large histories.

## Definition of done

- [ ] Context policy is immutable and pinned through `AgentVersion`.
- [ ] The application uses provider-independent token and summarizer ports.
- [ ] Context output never exceeds its declared budget.
- [ ] Current input and designated mandatory context are never silently dropped.
- [ ] Older omitted history is represented by a provenance-bearing summary.
- [ ] Summaries do not mutate visible conversation history.
- [ ] Repeated builds reuse a compatible summary.
- [ ] Incompatible policy/version summaries are not reused.
- [ ] Migration upgrade and downgrade pass.
- [ ] Ruff, strict mypy, unit, integration, architecture, and full tests pass.
- [ ] Documentation and `PROGRESS.md` match actual behavior.

## Suggested commit

`feat(context): add token-budgeted conversation context management`

## Earn

You can explain how a shared chat platform assembles reproducible bounded
context, why summaries are derived artifacts rather than messages, and how the
system fails safely when mandatory context cannot fit.

## Assumptions to revisit

- Token-count accuracy will improve when a concrete model provider is selected.
- Summary chaining may be needed for very long histories.
- Agent versions will later add prompt, model, router, tool, policy, and cost
  configuration.
