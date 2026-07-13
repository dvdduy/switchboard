# Evaluation Strategy

## Purpose

Switchboard evaluation answers two different questions:

1. **Before release:** Did a candidate change degrade expected agent behavior?
2. **After release:** Is the candidate behaving safely and reliably on live-like traffic?

Offline evaluation blocks a release. Live rollout signals trigger pause or rollback. They are related but not interchangeable.

## Eval bundle

Every run pins:

- dataset version;
- case IDs and expected behavior;
- agent version;
- router version;
- model provider/model/configuration;
- tool versions or tool simulators;
- policy version;
- deterministic evaluator code version;
- judge model and prompt version;
- seed and runtime settings;
- baseline run or release.

## Dataset categories

- clear single-tool routing;
- no-tool and out-of-domain requests;
- ambiguous requests requiring clarification;
- authorization and policy denial;
- confirmation-required mutations;
- multi-step workflows;
- unavailable or unhealthy tools;
- malformed model/tool output;
- long-context behavior;
- prompt-injection and malicious tool output;
- timeout, retry, and unknown-outcome scenarios;
- response quality and factual grounding against provided tool results.

## Evaluator tiers

### Tier 1 — deterministic

- expected routing outcome;
- selected/prohibited tool;
- confirmation requested;
- policy result;
- schema validity;
- no side effect in shadow mode;
- expected state transition;
- citations or result references when required.

### Tier 2 — programmatic semantic

- similarity to an allowed answer set;
- required fact coverage;
- contradiction against structured tool output;
- latency, token, and cost limits.

### Tier 3 — calibrated LLM judge

Used only where open-ended quality cannot be reduced to deterministic checks. Rubrics should be narrow and observable: relevance, completeness, clarity, and faithful use of supplied evidence.

A human-labelled calibration subset estimates agreement and variance. Judge scores are not treated as unquestionable truth.

## Routing metrics

- top-1 accuracy;
- top-k recall;
- accepted coverage;
- accuracy at accepted coverage;
- fallback and clarification rate;
- false-execution rate;
- confusion matrix by tool;
- confidence calibration error;
- unauthorized/unavailable selection rate.

A safe system may intentionally trade coverage for lower false execution.

## Agent and safety metrics

- required confirmation compliance;
- prohibited action rate;
- tool-argument validity;
- task completion;
- groundedness to tool results;
- policy violation rate;
- average judge score and variance;
- token and cost per case;
- latency per stage.

## CI gate

The CI report must include:

- baseline and candidate aggregate metrics;
- configured absolute and relative thresholds;
- changed cases;
- candidate trajectory versus expected behavior;
- evaluator disagreement;
- runtime and cost summary;
- pass/fail explanation.

Critical safety checks fail on any regression. Quality metrics may use bounded tolerance to account for nondeterminism.

## Shadow evaluation

A candidate router or agent configuration receives a copy of eligible traffic but cannot execute tools or affect the response. We compare:

- selected tool and routing outcome;
- confidence;
- estimated latency/cost;
- divergence from baseline;
- policy outcome.

Shadow mode produces evidence for canary admission but cannot prove full end-to-end correctness.

## Canary signals

- error and completion rate;
- fallback and clarification rate;
- tool failure and timeout rate;
- policy denial/violation rate;
- unknown-outcome rate;
- latency and time to first event;
- token and cost budgets;
- user correction/abandonment proxy in the simulator.

## Dataset evolution

New cases can come from:

- production-like incidents;
- routing ambiguity;
- evaluator disagreement;
- unknown outcomes;
- security review;
- newly registered tools.

Promotion requires sanitization, expected-behavior review, and a new immutable dataset version. The historical baseline remains reproducible.
