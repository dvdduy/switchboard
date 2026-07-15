from switchboard.bootstrap.demo_failures import (
    FAILURE_SCENARIOS,
    RecoveryMode,
    evidence_node_ids,
    selected_scenarios,
)


def test_failure_catalog_covers_day_10_scenarios_and_recovery_modes() -> None:
    assert [scenario.key for scenario in FAILURE_SCENARIOS] == [
        "client-disconnect-after-acceptance",
        "sse-reconnect",
        "runner-failure-after-partial-output",
        "malformed-model-action",
        "disabled-tool",
        "approval-expiry-or-argument-change",
        "duplicate-command-or-resume",
        "transaction-rollback",
        "unknown-mutation-outcome",
    ]
    assert {scenario.recovery_mode for scenario in FAILURE_SCENARIOS} == set(RecoveryMode)
    assert all(scenario.evidence for scenario in FAILURE_SCENARIOS)


def test_failure_evidence_selection_is_stable_and_deduplicated() -> None:
    selected = selected_scenarios(
        ("unknown-mutation-outcome", "client-disconnect-after-acceptance")
    )

    assert [scenario.key for scenario in selected] == [
        "client-disconnect-after-acceptance",
        "unknown-mutation-outcome",
    ]
    assert len(evidence_node_ids(selected)) == 2
