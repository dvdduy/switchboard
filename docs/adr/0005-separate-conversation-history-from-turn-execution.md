# ADR 0005 — Separate Conversation History from Turn Execution

**Status:** Accepted

## Context

Switchboard must preserve user-visible conversation history while processing
each user request through a durable workflow that may pause, fail, retry, or
outlive the originating HTTP request.

Treating a user message, a logical turn, and every physical execution attempt
as one mutable record would overwrite historical information and make retries
indistinguishable from repeated user requests.

Loading every message whenever a conversation receives a new message would
also make long-running conversations increasingly expensive and increase the
surface for concurrent-write conflicts.

## Decision

Switchboard uses the following distinct concepts:

- `Conversation` is the long-lived logical container and aggregate root.
- `Message` is an immutable, user-visible history item.
- `Turn` represents one logical request originating from one input message.
- `TurnAttempt` represents one physical attempt to process that turn.
- `AgentVersion` is immutable, and every turn records the exact version used.

A conversation stores its default agent version for future turns, while each
turn pins its actual agent version for historical reproducibility.

Messages are not loaded as one in-memory collection when appending. The
conversation repository locks the conversation row, allocates a positive
per-conversation sequence number, updates the next-sequence counter, and
inserts the message in one transaction.

PostgreSQL reinforces these invariants:

- `(conversation_id, sequence)` is unique.
- `input_message_id` is unique across turns.
- `(conversation_id, input_message_id)` references a message belonging to the
  same conversation.
- `(turn_id, attempt_number)` is unique.
- Lifecycle statuses and timestamps are constrained for consistency.

## Alternatives considered

### Load the full conversation aggregate

This provides a simple object model but makes every append proportional to
conversation-history size and increases unnecessary concurrency conflicts.

### Order messages by timestamp

Timestamps may collide and represent audit time rather than deterministic
conversation order.

### Order messages by UUID

Identity does not represent ordering.

### Store retries directly on the turn

A mutable attempt counter and last-error field would overwrite the history of
individual execution attempts.

### Always use the latest agent version

This would prevent reliable reproduction of historical behavior and undermine
evaluation, debugging, and rollout analysis.

## Consequences

### Positive

- Conversation history remains stable while execution state evolves.
- Worker retries do not appear as repeated user requests.
- Historical turns remain reproducible after agent upgrades.
- Message order is deterministic and database-enforced.
- Concurrent appends to one conversation are serialized safely.
- Different conversations can still progress concurrently.
- Persistence does not require loading complete conversation history.

### Negative

- The model contains more entities and tables.
- Appending a message requires a row lock on its conversation.
- One heavily active conversation becomes a serialized write boundary.
- Application and persistence translators must be maintained.
- Tenant ownership is currently validated by the application rather than a
  complete composite tenant foreign-key model.

## Follow-up

Future work will add:

- approval and unknown-outcome states;
- execution events;
- transactional outbox dispatch;
- tool invocations;
- explicit tenant isolation;
- agent prompt, model, tool, and policy configuration.