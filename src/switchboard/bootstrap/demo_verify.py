"""Run the focused Day 10 contract and operability verification matrix."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VerificationArea:
    """One release concern and the focused tests that prove it."""

    key: str
    claim: str
    evidence: tuple[str, ...]

    def to_safe_output(self) -> dict[str, object]:
        return asdict(self)


VERIFICATION_AREAS: tuple[VerificationArea, ...] = (
    VerificationArea(
        key="public-contract",
        claim="OpenAPI covers conversations, SSE, approvals, and sanitized stable errors.",
        evidence=(
            "tests/unit/api/test_conversations.py::"
            "test_openapi_documents_all_v1_paths_models_and_examples",
            "tests/integration/test_conversation_api.py::"
            "test_invalid_contract_inputs_are_sanitized_and_openapi_is_complete",
            "tests/integration/test_workflow_approval_api.py::"
            "test_workflow_approval_api_is_safe_additive_and_history_is_multi_turn",
        ),
    ),
    VerificationArea(
        key="migrations",
        claim="The full schema downgrades to base, upgrades to head, and backfills history.",
        evidence=("tests/integration/test_migrations.py",),
    ),
    VerificationArea(
        key="health-readiness",
        claim="Liveness is process-local and readiness reports dependency failure.",
        evidence=("tests/unit/test_health.py", "tests/unit/test_readiness.py"),
    ),
    VerificationArea(
        key="demo-controls",
        claim="Guarded reset/seed and both deterministic journeys are reproducible.",
        evidence=(
            "tests/integration/test_demo_environment.py",
            "tests/integration/test_demo.py",
        ),
    ),
    VerificationArea(
        key="redaction",
        claim="Sensitive schema fields require redaction and safe copies exclude values.",
        evidence=(
            "tests/unit/application/test_tool_manifest_validation.py::"
            "test_sensitive_schema_properties_require_matching_json_pointers",
            "tests/unit/application/test_tool_conformance.py::"
            "test_redaction_returns_an_immutable_copy_without_sensitive_values",
            "tests/unit/api/test_v1_contract_support.py::"
            "test_validation_errors_are_stable_and_do_not_echo_rejected_content",
        ),
    ),
    VerificationArea(
        key="bounded-execution",
        claim="Context, orchestration steps, and workflow mutations have enforced bounds.",
        evidence=(
            "tests/unit/application/test_context_assembler.py::"
            "test_varied_histories_never_exceed_the_declared_budget",
            "tests/unit/adapters/test_langgraph_orchestration.py::"
            "test_step_limit_blocks_work_before_the_next_bounded_node",
            "tests/integration/test_freeze_workflow_mutation_plan.py::"
            "test_planner_rejects_more_than_the_mutation_bound",
        ),
    ),
    VerificationArea(
        key="compose-contract",
        claim="The runtime image contains migrations and API/worker wait for schema head.",
        evidence=("tests/unit/test_compose_contract.py",),
    ),
)


def selected_areas(keys: Sequence[str]) -> tuple[VerificationArea, ...]:
    """Return requested areas in stable catalog order."""

    requested = set(keys)
    return tuple(area for area in VERIFICATION_AREAS if not requested or area.key in requested)


def evidence_node_ids(areas: Sequence[VerificationArea]) -> tuple[str, ...]:
    """Deduplicate pytest node IDs without changing their explanatory order."""

    return tuple(dict.fromkeys(node_id for area in areas for node_id in area.evidence))


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _docker_command() -> tuple[str, ...] | None:
    if shutil.which("docker") is not None:
        return ("docker",)
    if sys.platform != "win32" or shutil.which("wsl.exe") is None:
        return None
    available = subprocess.run(
        ("wsl.exe", "docker", "version", "--format", "{{.Server.Version}}"),
        check=False,
        capture_output=True,
        text=True,
    )
    if available.returncode != 0:
        return None
    return (
        "wsl.exe",
        "env",
        "SWITCHBOARD_POSTGRES_PORT=15432",
        "SWITCHBOARD_REDIS_PORT=16379",
        "SWITCHBOARD_API_PORT=18000",
        "docker",
    )


def _print_catalog(areas: Sequence[VerificationArea]) -> None:
    docker_command = _docker_command()
    output = {
        "areas": [area.to_safe_output() for area in areas],
        "compose_smoke": {
            "available": docker_command is not None,
            "backend": None if docker_command is None else docker_command[0],
            "isolated_project": "switchboard-day10-smoke",
            "production_capacity_claim": False,
        },
    }
    print(json.dumps(output, indent=2), flush=True)


def _run_compose_smoke() -> None:
    """Build a clean isolated stack, probe health, and remove its volumes."""

    docker_command = _docker_command()
    if docker_command is None:
        raise RuntimeError("Docker CLI is unavailable; Compose smoke was not run")

    root = _repository_root()
    base = (*docker_command, "compose", "--project-name", "switchboard-day10-smoke")
    environment = os.environ.copy()
    environment.update(
        {
            "SWITCHBOARD_POSTGRES_PORT": "15432",
            "SWITCHBOARD_REDIS_PORT": "16379",
            "SWITCHBOARD_API_PORT": "18000",
        }
    )
    try:
        subprocess.run((*base, "config", "--quiet"), cwd=root, env=environment, check=True)
        subprocess.run(
            (*base, "up", "--build", "--wait"),
            cwd=root,
            env=environment,
            check=True,
        )
        for endpoint in ("live", "ready"):
            with urllib.request.urlopen(
                f"http://127.0.0.1:18000/health/{endpoint}", timeout=5
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"Compose {endpoint} probe returned HTTP {response.status}")
    finally:
        subprocess.run(
            (*base, "down", "--volumes", "--remove-orphans"),
            cwd=root,
            env=environment,
            check=False,
        )


def main() -> None:
    """List or execute focused verification, with optional isolated Compose smoke."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--area",
        action="append",
        choices=tuple(area.key for area in VERIFICATION_AREAS),
        default=[],
        help="run only this area; repeat to select more than one",
    )
    parser.add_argument("--list", action="store_true", help="print evidence without running it")
    parser.add_argument(
        "--compose",
        action="store_true",
        help="also build, probe, and remove an isolated clean Compose stack",
    )
    args = parser.parse_args()
    areas = selected_areas(args.area)
    _print_catalog(areas)
    if args.list:
        return

    command = (sys.executable, "-m", "pytest", *evidence_node_ids(areas), "-q")
    completed = subprocess.run(command, cwd=_repository_root(), check=False)
    if completed.returncode != 0:
        parser.exit(completed.returncode, "verification evidence did not pass\n")
    if args.compose:
        try:
            _run_compose_smoke()
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            parser.exit(2, f"Compose smoke failed: {error}\n")


if __name__ == "__main__":
    main()
