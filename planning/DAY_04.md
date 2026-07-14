# Day 4 — Token-Budgeted Context Window Management

**Status:** Complete
**Phase:** Phase 1 — Conversation Platform Foundations
**Prerequisites:** Days 2–3 complete

## Goal

Build a deterministic, provider-independent context assembly capability that
keeps model input within a declared token budget, preserves the current user
request and the Phase 1 mandatory recent context, summarizes an older
conversation prefix instead of silently dropping it, and records enough
provenance to explain what the model saw.

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

Day 3 is committed as `3797f8e`, its planning closure is included in `c3747f5`,
the working tree was clean at Day 4 kickoff, and the baseline quality gate passed
with 103 tests, Ruff, and strict mypy.

## Accepted direction

1. Introduce an immutable `ContextPolicy` owned by `AgentVersion`.
2. Separate the total model window from:
   - reserved output tokens;
   - fixed instruction/tool overhead;
   - maximum summary tokens when summarization is required;
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
   - fixed instruction/tool capacity represented in budget metadata;
   - latest valid summary for an older prefix, when needed;
   - the newest contiguous message suffix that fits;
   - the current input message and configured recent-message floor as mandatory
     context.
7. Do not rewrite conversation history or insert a summary as a user-visible
   `Message`.
8. If mandatory items cannot fit, raise a structured
   `ContextBudgetExceededError`; never silently omit them.
9. Context assembly is deterministic for pinned messages, policy, counter, and
   summary artifact.
10. A turn reads messages only through its input-message sequence. Concurrent
    messages committed later must not change the context for that pinned turn.
11. Summary creation is lazy. No background task or eager compaction is added.
12. Phase 1 summaries cover a source-message prefix beginning at sequence 1;
    summaries do not summarize prior summaries.

The exact tokenizer and summarizer adapters are replaceable. The domain and
application contracts must not import a model-provider SDK.

## Resolved Phase 1 contract

### Context policy

`ContextPolicy` is stored as typed relational columns on immutable
`AgentVersion`, not as a JSON object. It contains:

- `model_window_tokens`;
- `reserved_output_tokens`;
- `fixed_overhead_tokens` for instructions, tool schemas, and framing not
  represented as conversation items;
- `summary_max_tokens` reserved only when an older prefix must be summarized;
- `minimum_recent_messages`, including the current input message.

The available conversation-input budget is:

```text
model_window_tokens - reserved_output_tokens - fixed_overhead_tokens
```

All fields are positive. Reserved output plus fixed overhead must leave a
positive conversation budget, and `summary_max_tokens` must be smaller than that
budget. Runtime message sizes may still make a valid policy impossible for a
particular turn; that produces `ContextBudgetExceededError`.

Existing agent versions are backfilled with the provider-neutral compatibility
profile `phase1-default-v1`:

```text
model window:            4096
reserved output:          512
fixed overhead:           256
maximum summary:          256
minimum recent messages:    1
```

These values are deterministic development defaults, not a claim about a future
production model.

### Token accounting

`TokenCounter` counts complete context-item candidates, including their kind,
role/framing, and content. It exposes a stable strategy/version identifier used
in provenance. A nonblank context item must receive a positive count. The
deterministic Phase 1 adapter is replaceable and is not presented as
provider-accurate.

When summarization is needed, the assembler reserves the full
`summary_max_tokens` before choosing the newest contiguous suffix. The summary
must fit that reservation. Unused summary capacity is not reclaimed in Phase 1;
this favors a simple deterministic guarantee over maximal packing.

### Mandatory context

The current input and the configured newest `minimum_recent_messages` are the
only mandatory context in Phase 1. They remain verbatim and in sequence order.
If they cannot fit, the build fails explicitly. Arbitrary semantic importance,
user-pinned memories, and guaranteed preservation of an unspecified older fact
are deferred rather than implied.

### Snapshot and summary provenance

The turn's input message determines an inclusive message-sequence cutoff. Context
assembly and summary coverage ignore every later message, even when it was
committed before the build began.

A Phase 1 summary covers exactly the contiguous source prefix
`[1, through_sequence]`. It records conversation ID, pinned agent-version ID,
coverage, content, estimated tokens, summarizer version, token-counter version,
and creation time. Reuse requires all provenance to be compatible.

One authoritative artifact exists for the same conversation, agent version,
coverage, summarizer version, and counter version. Concurrent creators use a
database uniqueness constraint plus conflict-safe insert/re-read; they do not
recover by catching an integrity error in an invalidated transaction.

## Budget examples

The following examples use a small illustrative policy: model window 20,
reserved output 4, fixed overhead 3, summary maximum 4, minimum recent messages
2. The available conversation budget is 13 tokens. Counts include item framing.

### Short context

Message counts `[2, 3, 3]` total 8. All messages through the input cutoff are
included verbatim; no summary is created.

### Near-limit context

Message counts `[4, 4, 5]` total 13. All messages fit exactly. The assembler does
not summarize merely because the context is close to the limit.

### Over-limit context

Message counts `[3, 3, 3, 4, 4]` total 17. Four tokens are reserved for a summary,
leaving nine for the suffix. Messages 4–5 remain verbatim and messages 1–3 become
one summary of at most four tokens. Used conversation tokens remain at most 13.

### Impossible mandatory context

If the two mandatory recent messages require 14 tokens, they exceed the available
budget of 13. The assembler raises `ContextBudgetExceededError`; it does not drop
either mandatory message or attempt an unbounded summary.

### Concurrent later history

If a turn's input is message 6 while messages 7–8 are already committed, the
build reads only sequences 1–6. Rebuilding that turn produces the same inputs.

## Build checkpoints

### Checkpoint 0 — Reconcile Day 3 and define the budget contract

- [x] verify Day 3 is committed and the working tree is understood;
- [x] inspect current `AgentVersion`, message repositories, and API DTOs;
- [x] write examples for short, near-limit, over-limit, and impossible contexts;
- [x] decide the initial token-budget vocabulary and summary provenance;
- [x] define the pinned message cutoff and concurrency boundary;
- [x] update the plan before code.

### Checkpoint 1 — Context policy and token-counting domain contract

Build:

- [x] immutable `ContextPolicy`;
- [x] validated positive token-budget fields;
- [x] derived available-input budget calculation;
- [x] `TokenCounter` application port;
- [x] context item/result value objects with source metadata;
- [x] `ContextBudgetExceededError`;
- [x] unit tests for invalid budgets, reserved capacity, and deterministic counting.

The policy must make impossible configurations invalid, such as reserved output
plus fixed overhead consuming the entire model window.

`ContextItem` and `BuiltContext` expose accounting and provenance only; they do
not add an HTTP contract or provider-specific request object.

### Checkpoint 2 — Agent-version and summary persistence

Completed:

- [x] context policy on immutable `AgentVersion`;
- [x] `ConversationSummary` domain entity and typed identifier;
- [x] summary repository port and SQLAlchemy adapter;
- [x] relational constraints proving:
  - positive sequence coverage;
  - `from_sequence = 1` and `through_sequence >= from_sequence`;
  - summary ownership by one conversation;
  - nonblank content;
  - positive estimated token count;
  - one authoritative artifact per compatible provenance and coverage;
- [x] migration with safe defaults/backfill for existing agent versions;
- [x] translators and migration round-trip tests;
- [x] inclusive message-cutoff read and PostgreSQL concurrency coverage.

Add an inclusive `list_messages_through(conversation_id, through_sequence)` read
so a pinned turn cannot observe later concurrent history. Summary coverage
endpoints should reference messages belonging to the same conversation where a
relational constraint can express that invariant.

Historical turns must continue resolving their pinned agent version and context
policy.

### Checkpoint 3 — Deterministic context assembler

Completed a framework-independent service that:

1. [x] receives ordered immutable messages;
2. [x] computes the available conversation budget;
3. [x] includes the newest contiguous suffix that fits;
4. [x] identifies the omitted older prefix;
5. [x] uses a supplied summary for exactly that covered prefix;
6. [x] produces ordered context items and token accounting;
7. [x] never exceeds the budget;
8. [x] never omits the current input;
9. [x] exposes provenance showing summary coverage and included message sequences.

This service receives already-loaded immutable inputs. It does not open a unit of
work, persist a summary, or call a summarizer.

Property-style tests prove the output never exceeds the budget across varied
histories.

### Checkpoint 4 — Summary creation and build-context use case

Completed an application workflow that:

- [x] loads the pinned `AgentVersion`;
- [x] resolves the turn input-message sequence and reads no later history;
- [x] reuses a valid existing summary when possible;
- [x] creates a new deterministic summary when an older prefix requires it;
- [x] persists the summary before returning context;
- [x] handles concurrent attempts to summarize the same coverage without duplicate
  authoritative artifacts;
- [x] returns a complete `BuiltContext` result for the later orchestrator.

Separate short transactions surround durable summary writes. No database
transaction remains open during summarization. Tests prove summarizer failure or
task cancellation leaves no partial summary.

### Checkpoint 5 — Integration, diagnostics, and documentation

Proved with PostgreSQL tests that:

- [x] a long conversation produces a persisted summary plus recent suffix;
- [x] the current input and configured recent-message floor survive;
- [x] repeated context builds reuse the same valid summary;
- [x] changed history beyond the covered sequence does not invalidate the prefix;
- [x] messages after the turn input cutoff do not enter that turn's context;
- [x] a changed agent context policy does not silently reuse an incompatible
  summary;
- [x] rollback leaves no partial summary;
- [x] context output remains under budget after a new session.

Architecture, domain model, requirements evidence, `README.md`, `PROGRESS.md`,
and this plan record the actual implementation and deferred scope.

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
- concurrent creators converge on one authoritative summary;
- summarizer failure and cancellation persist no partial artifact;
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
- Cross-team summary access must be rejected.
- Summary text is untrusted conversation-derived content and must never be
  promoted to system instructions, policy, or authorization evidence.
- Repository and application logs record identities, coverage, versions, and
  counts—not raw messages or summaries.

## Out of scope

- provider-specific production tokenizers;
- paid or nondeterministic LLM summarization;
- vector retrieval or long-term semantic memory;
- cross-conversation memory;
- user-editable memories;
- arbitrary semantic critical-fact detection or pinning;
- prompt/model configuration beyond the context policy required today;
- background summary compaction;
- summary chaining or summary-of-summary compaction;
- summary retention and deletion policy;
- performance optimization for very large histories.

## Definition of done

- [x] Context policy is immutable and pinned through `AgentVersion`.
- [x] The application uses provider-independent token and summarizer ports.
- [x] Context output never exceeds its declared budget.
- [x] Current input and designated mandatory context are never silently dropped.
- [x] Older omitted history is represented by a provenance-bearing summary.
- [x] Summaries do not mutate visible conversation history.
- [x] Repeated builds reuse a compatible summary.
- [x] Incompatible policy/version summaries are not reused.
- [x] Migration upgrade and downgrade pass.
- [x] Ruff, strict mypy, unit, integration, architecture, and full tests pass.
- [x] Documentation and `PROGRESS.md` match actual behavior.

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
