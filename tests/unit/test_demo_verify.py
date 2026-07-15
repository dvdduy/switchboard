from switchboard.bootstrap.demo_verify import (
    VERIFICATION_AREAS,
    evidence_node_ids,
    selected_areas,
)


def test_verification_catalog_covers_checkpoint_5_areas() -> None:
    assert [area.key for area in VERIFICATION_AREAS] == [
        "public-contract",
        "migrations",
        "health-readiness",
        "demo-controls",
        "redaction",
        "bounded-execution",
        "compose-contract",
    ]
    assert all(area.evidence for area in VERIFICATION_AREAS)


def test_verification_selection_is_stable_and_deduplicated() -> None:
    selected = selected_areas(("compose-contract", "public-contract"))

    assert [area.key for area in selected] == ["public-contract", "compose-contract"]
    assert len(evidence_node_ids(selected)) == 4
