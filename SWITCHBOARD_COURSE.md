# Switchboard
### A Chat & Agent Platform — Tool Routing, Evaluation, and Shared Infrastructure — reverse-engineered from Asana's Vancouver Chat Platform posting

**Trigger phrase:** `Switchboard, Day N, let's go.`
**Status:** Active — Day 8 complete; Day 9 planned.
**Track target:** Asana — Senior Software Engineer, Chat Platform (Vancouver)

---

## Why this project, and why now

This is a genuinely new shape, not another surface on Lattice, Ledger, or Beacon. Those three cover product engineering, revenue/billing correctness, and logging/experimentation/observability infrastructure respectively. This posting is about something none of them touch: **the platform underneath AI chat and agent products** — tool selection, agent evaluation, conversation state, and the shared APIs other Asana teams build chat features on top of. Lattice's MCP server (Gateway phase) exposes *Lattice's* graph as tools to an agent; this posting is about building the platform that decides *which* tool to call, *how well* the agent is performing, and *what* every product team gets for free instead of re-solving it themselves. That's a different problem, one level up.

It also isn't a fit for paused Mosaic, even though both involve LangGraph-style agent orchestration and an eval-as-CI-gate pattern. Mosaic is scoped to a specific Workday posting (a semantic data layer with agentic access), built and paused as its own thing. Reusing the *pattern* (eval-as-CI-gate) here is natural and worth doing — but building it fresh, scoped to Asana's chat-platform framing, keeps each project honestly tied to the posting it targets rather than quietly merging two different companies' curricula.

Deliberately, this track does **not** front-load a new language. Every other active/horizon track uses a new-language ramp as its skill-gap story (Go for Forge, TypeScript/React for Steward, C#/.NET for Conduit, Rust for Sentinel, Scala for Ledger/Lattice-Gateway). This posting's actual ask is depth in AI/agent orchestration — Python + LangGraph, the same stack as Mosaic — so Switchboard is where that depth gets demonstrated properly, rather than diluted across a dozen other priorities.

---

## 48-Point Specification

### I. Product Vision & Context
1. **Elevator pitch:** A backend platform providing tool selection/routing, agent evaluation, conversation state management, and shared chat APIs — the infrastructure a chat/agent product team builds on top of instead of rebuilding.
2. **Target scenario:** A product team wires a new chat feature against the shared conversation API, registers a new tool in the tool registry, and gets routing, guardrails, observability, and quality evaluation for free — without touching platform internals.
3. **Why this project maps to the posting:** hits tool selection/evaluation, shared APIs/product-enablement, an AI evaluation platform, observability/state-management/latency for chat systems, and the senior mentoring/simplification bar — everything named in the JD.
4. **Non-goals:** not a specific chat *product* (that's what other teams build on top), not a general-purpose ML training platform, not a new LLM itself — kept lean so the platform-and-evaluation core stays the star.

### II. Functional Requirements
5. A shared, versioned conversation API: create/continue a conversation, stream a response, persist multi-turn state.
6. A tool registry: tools register their schema, description, and scope, available for selection at runtime.
7. Tool selection/routing: given a user turn, select the correct tool(s) with a confidence score and an explicit no-match/fallback path.
8. Guardrails: any mutating tool call requires an explicit confirmation gate, matching the read-only-by-default stance used across every other track.
9. An AI evaluation platform: golden-dataset-based scoring of agent responses and tool-selection accuracy.
10. Regression detection: an eval run gating CI, failing a change that degrades agent quality below a threshold.
11. Observability: per-turn tracing of tool calls, latency, and reasoning steps, with a debugging view to replay a problematic conversation.
12. Staged rollout: canary a new tool integration or model version, with automatic rollback on a regression signal.

### III. Non-Functional Requirements
13. Conversation state must survive a mid-turn crash without corruption or duplicate tool execution (idempotent retries).
14. Tool selection must degrade gracefully — an ambiguous or unmatched query should surface a fallback, never a silent wrong-tool call.
15. Eval regression detection must be a CI gate, not a dashboard someone has to remember to check.
16. Per-turn latency must be measured and attributable to a specific stage (tool selection vs. main generation vs. tool execution).
17. A staged rollout must be able to auto-rollback within a bounded time window of a detected regression.
18. Every durable platform-capability decision is documented (design doc or ADR), not left implicit in code.

### IV. System Architecture
19. **Backend:** Python / FastAPI — consistent with your strength, and the natural stack for this domain.
20. **Agent orchestration:** LangGraph — same stack as Mosaic, deepened here rather than re-derived.
21. **Tool selection:** embedding-based candidate retrieval + LLM re-ranking/selection, with a confidence threshold and explicit fallback.
22. **State store:** PostgreSQL for conversation/message/tool-call persistence; Redis for ephemeral session/context-window state.
23. **Streaming:** Server-Sent Events (or WebSockets) for token-by-token assistant response streaming.
24. **Eval harness:** golden datasets + rubric-based and LLM-as-judge scoring, wired into CI as a regression gate.
25. Local-first: the full platform runs via Docker Compose (Postgres, Redis, FastAPI service, eval runner); no proprietary chat-platform dependency required to develop or demo.

### V. Conversation & State Management
26. Core entities: `Conversation`, `Message`, `ToolCall`, `ToolRegistration` — with `ToolCall` capturing tool name, arguments, result, and whether it required confirmation.
27. Context-window management: a summarization/truncation strategy tested against a defined token budget, so long conversations don't silently break.
28. Multi-turn state carried correctly across tool calls within a single conversation, including partial/interrupted tool sequences.
29. Idempotent tool-call retries: a crash mid-tool-call must not re-execute a mutating action twice.
30. Streaming responses that can be safely interrupted and resumed without corrupting conversation state.

### VI. Tool Selection & Routing
31. A written design doc on the tool-selection problem — alternatives considered (pure rules, embedding similarity, LLM-based selection, hybrid) — before implementation, mirroring how a real platform team would approach it.
32. Embedding-based candidate retrieval narrowing the tool registry to a small relevant set before a more expensive LLM-based final selection.
33. A confidence score attached to every routing decision, with a documented threshold below which the system falls back to a clarifying question rather than guessing.
34. A small eval harness specifically for routing accuracy (query → correct-tool pairs), separate from the broader agent-quality eval platform.
35. New tools can be registered by other teams without platform-team code changes — the actual "shared infrastructure" claim, made real.

### VII. AI Evaluation Platform
36. Golden datasets: curated (input, expected-behavior) pairs covering both tool-selection accuracy and overall response quality.
37. Rubric-based scoring for structured criteria (did it call the right tool, did it ask for confirmation when required) plus LLM-as-judge scoring for open-ended quality.
38. Eval runs wired into CI: a PR that regresses any tracked metric below its threshold fails the build.
39. An eval dashboard tracking agent-quality metrics over time, sliced per tool and per feature area.
40. Eval-as-CI-gate is deliberately the same pattern used in Mosaic's design — implemented independently here, but with the reuse explicitly noted as a portfolio-wide engineering habit.

### VIII. Observability & Rollout Safety
41. Per-turn tracing capturing tool calls, latency breakdown, and reasoning steps, exportable to a debugging view.
42. A "replay this conversation" debugging tool for engineers investigating a reported issue.
43. Staged rollout for new tool integrations or model versions — canary a percentage, auto-rollback on a regression signal from the eval platform or live metrics.
44. Latency attribution across pipeline stages (tool selection, main generation, tool execution) so a slow turn can be diagnosed, not just observed.

### IX. Leadership & Simplification
45. A second design doc translating an ambiguous product need (e.g., "how do we let a new team register a tool safely") into a durable platform capability — direct evidence of "translate product needs into durable platform capabilities."
46. A documented simplification pass: refactor one real piece of the system (e.g., collapsing a two-step tool-selection call into one), with a before/after ADR — direct evidence of "identify opportunities to simplify systems and increase engineering leverage."

### X. Resume & Interview Value
47. Interview-readiness milestones at Day 10 (conversation/tool-registry MVP), Day 20 (routing/observability/rollout complete), and Day 30 (eval platform + capstone).
48. A quantified platform story: routing accuracy on a golden dataset, an eval-gated regression caught in CI before merge, and a staged rollout auto-rolled-back on an induced regression — all demonstrated, not asserted.

---

## Tech Stack Summary

| Layer | Choice | Why |
|---|---|---|
| Backend | Python / FastAPI | Reuses your strength; this track's depth is in AI/agent orchestration, not a new language |
| Agent orchestration | LangGraph | Same stack as Mosaic, deepened rather than re-derived |
| Tool selection | Embedding retrieval + LLM re-ranking | Matches "tool selection and evaluation" from the JD directly |
| State store | PostgreSQL + Redis | Durable conversation/tool-call history + ephemeral session state |
| Streaming | Server-Sent Events | Token-by-token response streaming without websocket complexity where SSE suffices |
| Eval | Golden datasets + rubric/LLM-as-judge scoring, CI-gated | "Frontier practices in agent evaluation," made a hard gate rather than a dashboard |
| CI/CD | GitHub Actions | Lint, typecheck, eval-regression gate, staged rollout simulation |
| Local env | Docker Compose | Postgres + Redis + FastAPI service + eval runner, no proprietary dependency required |

**Honest gaps, documented up front:**
- No real production-scale multi-model routing (e.g., cost-based routing across many LLM providers) — tool selection is the routing problem tackled here, not model routing; documented as a deliberate scope boundary.
- LLM-as-judge scoring is a real but simplified implementation — not a full frontier-lab eval research pipeline, named honestly as a portfolio-appropriate scope.
- No real multi-tenant, multi-team production rollout — staged rollout and rollback are demonstrated locally with simulated traffic, not at Asana's actual scale.

---

## 30-Session Curriculum

### Phase 1 — Conversation Platform Foundations (Days 1–10)

**Day 1 — Scaffold**
Learn: FastAPI + Postgres + Redis project layout for a chat-platform service.
Build: repo skeleton, Docker Compose, CI pipeline.
Commit: green CI on an empty scaffold.
Earn: foundation in place for "the intelligence and shared infrastructure behind the conversation."

**Day 2 — Conversation data model**
Learn: schema design for multi-turn conversation state.
Build: `Conversation`, `Message`, `ToolCall`, `ToolRegistration` tables.
Commit: migrations + seed data.
Earn: `ToolCall` capturing confirmation-required state from day one — the guardrail habit baked into the schema, not bolted on later.

**Day 3 — Streaming responses**
Learn: Server-Sent Events for token-by-token streaming.
Build: a streaming endpoint returning a simulated assistant response token-by-token.
Commit: a client receives and reconstructs a streamed response correctly.
Earn: first working piece of "enterprise-grade chat experiences."

**Day 4 — Context window management**
Learn: summarization/truncation strategies for long conversations.
Build: a token-budget-aware context manager that summarizes older turns rather than dropping them silently.
Commit: a long-conversation test stays within budget without losing critical earlier context.
Earn: direct answer to "state management" from the JD's observability/quality list.

**Day 5 — Tool registry**
Learn: registry pattern for pluggable tools.
Build: a registry where tools declare schema, description, and scope.
Commit: a new tool can be registered without touching existing routing code.
Earn: the mechanical backbone of "shared infrastructure... without rebuilding the same systems."

**Day 6 — Shared conversation API**
Learn: versioned API design for internal-team consumers (same discipline as Ledger's CBBL building-blocks phase and Lattice's Gateway phase).
Build: a versioned, documented API for creating/continuing conversations.
Commit: an external test client builds a full conversation using only the public API surface.
Earn: direct evidence of "shared APIs, services, and abstractions."

**Day 7 — LangGraph agent loop**
Learn: ReAct-style agent loop construction in LangGraph.
Build: a basic agent loop wired to the Day 5 tool registry.
Commit: an agent correctly calls a registered tool and incorporates the result into its response.
Earn: first end-to-end agent behavior, not just scaffolding.

**Day 8 — Guardrails**
Learn: authorization, policy, confirmation, and execution are separate durable
facts; approval must bind the exact action fingerprint and survive races.
Build: pure policy evaluation, immutable audit, expiring fingerprint-bound
approval, safe public decisions, and atomic resume/cancellation events.
Commit: a mutating tool call remains inert before approval and crosses the
logical dispatch boundary once in tested concurrent flows.
Earn: explain why durable confirmation is a platform state machine rather than
a prompt instruction or in-memory graph pause.

**Day 9 — Multi-tool orchestration**
Learn: state handling across multi-step tool sequences.
Build: a conversation spanning multiple tool calls across multiple turns, with state carried correctly.
Commit: an interrupted multi-tool sequence resumes correctly rather than restarting or corrupting.
Earn: proof the conversation/tool-call model handles real agent complexity, not just a single-call demo.

**Day 10 — Phase 1 integration + checkpoint**
Learn: nothing new — consolidation day.
Build: end-to-end demo: start a conversation, stream a response, call a tool through the confirmation gate, maintain state across turns.
Commit: tagged `v0.1-platform`.
Earn: **Checkpoint 1** — rehearsed 60-second platform walkthrough.

---

### Phase 2 — Tool Selection, Observability, Rollout Safety (Days 11–20)

**Day 11 — Tool-selection design doc**
Learn: design-doc structure for an ambiguous routing problem.
Build: a real design doc on tool selection — alternatives (rules, embedding similarity, LLM-based, hybrid), trade-offs, recommendation.
Commit: design doc committed to the repo.
Earn: direct evidence of navigating "complex architectural boundaries" before jumping to code.

**Day 12 — Semantic tool routing**
Learn: embedding-based retrieval + LLM re-ranking.
Build: candidate-tool retrieval via embeddings, final selection via LLM re-ranking, with a confidence score.
Commit: a query correctly routes to the right tool with a confidence score attached.
Earn: **the single strongest "tool selection" talking point** — implemented, not just designed.

**Day 13 — Routing eval harness**
Learn: small-scale eval-dataset construction.
Build: a golden dataset of (query → correct tool) pairs, measuring routing accuracy.
Commit: a routing-accuracy number against the golden dataset.
Earn: sets up Phase 3's full eval platform with a working, scoped-down proof of concept.

**Day 14 — Agent/chat observability I**
Learn: tracing patterns for agent trajectories.
Build: per-turn tracing of tool calls, latency, and reasoning steps.
Commit: a trace correctly shows every tool call and its latency for a multi-tool conversation.
Earn: direct answer to "observability... including areas like latency, availability, state management, and debugging."

**Day 15 — Agent/chat observability II**
Learn: debugging-tool UX for trace replay.
Build: a "replay this conversation" view for engineers investigating an issue.
Commit: a problematic conversation is fully reconstructable and inspectable from its trace.
Earn: direct answer to the "debugging" half of the same JD line.

**Day 16 — Latency & availability**
Learn: latency-attribution techniques across pipeline stages.
Build: benchmark tool-selection latency vs. main-generation latency vs. tool-execution latency; add caching where safe.
Commit: before/after latency numbers after one targeted optimization.
Earn: quantified performance story distinguishing which stage was actually slow.

**Day 17 — Rollout safety**
Learn: canary rollout + auto-rollback patterns.
Build: staged rollout of a new tool integration, with auto-rollback triggered by a regression signal.
Commit: an induced regression triggers rollback within a bounded time window.
Earn: direct evidence of "rollout safety" from the JD's production-quality-instincts list.

**Day 18 — State management under failure**
Learn: nothing new — applying Day 9's idempotency lessons to a harder failure mode.
Build: conversation-state recovery after a simulated mid-turn crash; idempotent tool-call retries verified.
Commit: a crash-and-recover test shows no duplicate mutating tool execution.
Earn: the reliability half of "backend engineering fundamentals... reliable, maintainable distributed systems."

**Day 19 — Second design doc**
Learn: nothing new — applying Day 11's design-doc skill to a platform-capability question.
Build: a design doc on how a new product team registers a tool safely without platform-team involvement.
Commit: design doc committed to the repo.
Earn: direct evidence of "translate product needs into durable platform capabilities."

**Day 20 — Phase 2 integration + checkpoint**
Learn: nothing new — consolidation day.
Build: full demo — tool routing with confidence/fallback, a full observability trace replay, a staged rollout with an induced regression auto-rolled-back.
Commit: tagged `v0.2-routing`.
Earn: **Checkpoint 2** — rehearsed walkthrough of the routing/observability/rollout story.

---

### Phase 3 — AI Evaluation Platform, Leadership, Capstone (Days 21–30)

**Day 21 — Eval platform scaffold**
Learn: golden-dataset structure and eval-run harness design.
Build: an eval harness that runs a golden dataset against the current agent and records results.
Commit: a baseline eval run produces a reproducible score.
Earn: foundation for "frontier practices in agent evaluation."

**Day 22 — Automated scoring**
Learn: rubric-based scoring + LLM-as-judge patterns.
Build: rubric scoring (did it call the right tool, did it correctly request confirmation) plus LLM-as-judge scoring for open-ended quality.
Commit: both scoring methods produce consistent, explainable scores on known-good and known-bad examples.
Earn: direct evidence of "ensure our AI products are reliable, accurate, and high-performing."

**Day 23 — Regression detection as a CI gate**
Learn: nothing new — wiring Day 22's scoring into CI.
Build: an eval run gating CI, failing a PR that regresses any tracked metric below threshold.
Commit: a deliberately-regressing change is caught and blocked by CI.
Earn: **the single strongest "AI evaluation platform" talking point** — a real gate, not a dashboard someone has to remember to check.

**Day 24 — Eval dashboard**
Learn: nothing new — reporting-view design applied to eval results.
Build: a dashboard tracking agent-quality metrics over time, sliced per tool and per feature area.
Commit: the dashboard correctly reflects a real regression-and-fix cycle.
Earn: visibility layer completing the eval platform story.

**Day 25 — Simplification pass**
Learn: nothing new — a deliberate refactor exercise.
Build: collapse a two-step piece of the tool-selection or eval pipeline into one, measurably simpler without losing correctness.
Commit: before/after ADR with a concrete simplification metric (fewer calls, less code, faster path).
Earn: direct, demonstrated evidence of "identify opportunities to simplify systems and increase engineering leverage" — the JD line most portfolio projects can only assert.

**Day 26 — Mentoring artifact**
Learn: nothing new — applying the established mentoring-artifact pattern here.
Build: a code review checklist and onboarding guide for a new engineer joining the Chat Platform team.
Commit: both documents committed to the repo.
Earn: direct evidence for "raise the effectiveness of the team by mentoring other engineers."

**Day 27 — Testing strategy**
Learn: contract testing for the shared conversation API; property testing for conversation-state invariants.
Build: contract tests, state-invariant property tests, and the eval-suite CI gate finalized together.
Commit: CI fails if any of the three is violated.
Earn: direct answer to "strong instincts for production quality, including... testing."

**Day 28 — CI/CD**
Learn: nothing new — full pipeline assembly.
Build: complete GitHub Actions pipeline: lint, typecheck, eval-regression gate, staged rollout simulation.
Commit: green pipeline on a real PR.
Earn: closes the loop on every production-quality instinct named in the JD.

**Day 29 — Capstone polish**
Learn: nothing new — polish day.
Build: architecture diagram, final README, ADR compilation.
Commit: tagged `v1.0-capstone`.
Earn: a complete, presentable artifact set.

**Day 30 — Capstone review + interview prep**
Learn: nothing new — review day.
Build: nothing new — compile design docs, ADRs, eval numbers, and the rollout/rollback demo into a rehearsed narrative.
Commit: final tag + walkthrough script.
Earn: **Checkpoint 3** — full rehearsed capstone walkthrough, design-doc-backed answers ready for every "why did you..." and "how would you simplify..." question.

---

## Interview Readiness Summary

By Day 30 you can walk into the Asana Chat Platform interview with:
- A working tool-selection/routing layer with a measured accuracy number on a golden dataset, plus an explicit confidence-threshold fallback — not a hand-wave answer to "how would you route between tools."
- An AI evaluation platform that's a real CI gate — a regression is caught and blocked before merge, demonstrated live.
- Full agent/chat observability: per-turn tracing, latency attribution across pipeline stages, and a working conversation-replay debugging tool.
- A staged rollout with a demonstrated auto-rollback on an induced regression — direct evidence of "rollout safety."
- A documented simplification pass with a before/after ADR — the rare, concrete answer to "identify opportunities to simplify systems and increase engineering leverage."
- Two design docs and a mentoring artifact, at the same senior tier as Ledger, Beacon, and Lattice's Account Management phase.
- The same read-only-by-default, confirmation-gated guardrail philosophy running through every other track in your portfolio, now applied to a fifth distinct system.
