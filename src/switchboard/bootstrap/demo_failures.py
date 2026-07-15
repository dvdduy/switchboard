"""Run the deterministic Day 10 failure and recovery evidence matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path


class RecoveryMode(StrEnum):
    """Who may safely initiate the next action after a failure."""

    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class FailureScenario:
    """One injected failure, its durable outcome, and its executable evidence."""

    key: str
    failure: str
    durable_outcome: str
    recovery_mode: RecoveryMode
    recovery: str
    evidence: tuple[str, ...]

    def to_safe_output(self) -> dict[str, object]:
        return asdict(self)


FAILURE_SCENARIOS: tuple[FailureScenario, ...] = (
    FailureScenario(
        key="client-disconnect-after-acceptance",
        failure="The observing HTTP client disconnects after durable acceptance.",
        durable_outcome="The received or running turn remains unchanged in PostgreSQL.",
        recovery_mode=RecoveryMode.AUTOMATIC,
        recovery="Reconnect and inspect the same turn; a transport disconnect is not cancellation.",
        evidence=(
            "tests/integration/test_turn_events_api.py::"
            "test_disconnecting_observer_does_not_mutate_running_turn",
        ),
    ),
    FailureScenario(
        key="sse-reconnect",
        failure="An SSE observer disconnects after a committed event.",
        durable_outcome="Replay resumes exclusively after the supplied sequence cursor.",
        recovery_mode=RecoveryMode.AUTOMATIC,
        recovery="Reconnect with the last committed sequence and reconstruct from durable events.",
        evidence=(
            "tests/integration/test_turn_events_api.py::"
            "test_terminal_stream_replays_remaining_events_and_closes",
            "tests/integration/test_demo.py::"
            "test_read_only_journey_crosses_public_and_trusted_boundaries",
        ),
    ),
    FailureScenario(
        key="runner-failure-after-partial-output",
        failure="Execution is cancelled after one or more response deltas commit.",
        durable_outcome="Committed deltas remain ordered and the turn closes with turn.failed.",
        recovery_mode=RecoveryMode.EXPLICIT,
        recovery="Inspect durable output, then create a new command if the user wants another run.",
        evidence=(
            "tests/integration/test_simulate_assistant_response.py::"
            "test_cancellation_after_partial_progress_records_durable_failure",
        ),
    ),
    FailureScenario(
        key="malformed-model-action",
        failure="The model fails the normalized structured-action contract.",
        durable_outcome=(
            "The adapter raises a safe error and a started turn records a safe failure."
        ),
        recovery_mode=RecoveryMode.EXPLICIT,
        recovery=(
            "Fix or replace the model boundary, then submit a new turn; do not resume the attempt."
        ),
        evidence=(
            "tests/unit/adapters/test_langgraph_orchestration.py::"
            "test_second_tool_request_is_rejected_without_a_second_dispatch",
            "tests/integration/test_run_turn.py::"
            "test_post_start_orchestration_failure_closes_turn_with_safe_event",
        ),
    ),
    FailureScenario(
        key="disabled-tool",
        failure="A selected tool version is disabled before dispatch.",
        durable_outcome="No adapter call occurs and pending workflow mutations are skipped.",
        recovery_mode=RecoveryMode.EXPLICIT,
        recovery="Select or enable an eligible version, then create a newly evaluated plan.",
        evidence=(
            "tests/integration/test_workflow_failure_matrix.py::"
            "test_disabled_tool_is_rejected_before_dispatch",
        ),
    ),
    FailureScenario(
        key="approval-expiry-or-argument-change",
        failure="Approval expires or frozen arguments change before dispatch.",
        durable_outcome="Expiry cancels; changed evidence blocks resume with zero adapter calls.",
        recovery_mode=RecoveryMode.EXPLICIT,
        recovery="Build and approve a fresh plan from current arguments and policy evidence.",
        evidence=(
            "tests/integration/test_workflow_failure_matrix.py::"
            "test_expired_approval_cancels_without_dispatch",
            "tests/integration/test_approval_concurrency.py::"
            "test_changed_action_or_disabled_version_blocks_resume[arguments]",
        ),
    ),
    FailureScenario(
        key="duplicate-command-or-resume",
        failure="The same logical command or workflow resume is delivered more than once.",
        durable_outcome="One durable graph and one logical mutation result are replayed.",
        recovery_mode=RecoveryMode.AUTOMATIC,
        recovery="Replay the stored receipt or terminal workflow evidence; do not redispatch.",
        evidence=(
            "tests/integration/test_conversation_api.py::"
            "test_concurrent_duplicate_create_has_one_public_result_and_graph",
            "tests/integration/test_approval_concurrency.py::"
            "test_concurrent_same_command_has_one_resume_and_one_dispatch",
            "tests/integration/test_demo.py::"
            "test_approval_workflow_recreates_runner_and_executes_mutations_once",
        ),
    ),
    FailureScenario(
        key="transaction-rollback",
        failure="The terminal success write violates a database invariant.",
        durable_outcome="False success rolls back before a separate transaction records failure.",
        recovery_mode=RecoveryMode.AUTOMATIC,
        recovery="Trust the durable failed state; investigate before issuing any new command.",
        evidence=(
            "tests/integration/test_run_turn.py::"
            "test_completion_write_failure_rolls_back_success_before_durable_failure",
        ),
    ),
    FailureScenario(
        key="unknown-mutation-outcome",
        failure="The adapter connection fails after mutation dispatch may have occurred.",
        durable_outcome=(
            "The step becomes unknown, later steps stop, and replay makes no adapter call."
        ),
        recovery_mode=RecoveryMode.MANUAL,
        recovery="Reconcile the external system by idempotency key before any deliberate retry.",
        evidence=(
            "tests/integration/test_workflow_failure_matrix.py::"
            "test_ambiguous_dispatch_becomes_unknown_and_is_never_retried",
        ),
    ),
)


def selected_scenarios(keys: Sequence[str]) -> tuple[FailureScenario, ...]:
    """Return the requested scenarios in stable catalog order."""

    requested = set(keys)
    return tuple(
        scenario for scenario in FAILURE_SCENARIOS if not requested or scenario.key in requested
    )


def evidence_node_ids(scenarios: Sequence[FailureScenario]) -> tuple[str, ...]:
    """Deduplicate pytest node IDs without changing their explanatory order."""

    return tuple(dict.fromkeys(node_id for scenario in scenarios for node_id in scenario.evidence))


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _print_catalog(scenarios: Sequence[FailureScenario]) -> None:
    print(json.dumps([scenario.to_safe_output() for scenario in scenarios], indent=2), flush=True)


def main() -> None:
    """List or execute the focused failure evidence through pytest."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(scenario.key for scenario in FAILURE_SCENARIOS),
        default=[],
        help="run only this scenario; repeat to select more than one",
    )
    parser.add_argument("--list", action="store_true", help="print evidence without running it")
    args = parser.parse_args()
    scenarios = selected_scenarios(args.scenario)
    _print_catalog(scenarios)
    if args.list:
        return

    command = (sys.executable, "-m", "pytest", *evidence_node_ids(scenarios), "-q")
    completed = subprocess.run(command, cwd=_repository_root(), check=False)
    if completed.returncode != 0:
        parser.exit(completed.returncode, "failure evidence did not pass\n")


if __name__ == "__main__":
    main()
