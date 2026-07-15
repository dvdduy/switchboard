import pytest

from switchboard.bootstrap.config import Settings
from switchboard.bootstrap.demo_environment import (
    SequenceIdGenerator,
    UnsafeDemoResetError,
    require_safe_demo_reset,
)


def settings(*, environment: str, database_url: str) -> Settings:
    return Settings.model_validate(
        {
            "environment": environment,
            "database_url": database_url,
            "redis_url": "redis://localhost:6379/0",
        }
    )


@pytest.mark.parametrize(
    ("environment", "database_url"),
    [
        (
            "local",
            "postgresql+psycopg://switchboard:switchboard@localhost:5432/switchboard",
        ),
        (
            "test",
            "postgresql+psycopg://switchboard:switchboard@127.0.0.1:5433/switchboard_test",
        ),
        (
            "local",
            "postgresql+psycopg://switchboard:switchboard@postgres:5432/switchboard",
        ),
    ],
)
def test_reset_guard_accepts_only_declared_local_targets(
    environment: str,
    database_url: str,
) -> None:
    require_safe_demo_reset(settings(environment=environment, database_url=database_url))


@pytest.mark.parametrize(
    ("environment", "database_url"),
    [
        (
            "production",
            "postgresql+psycopg://switchboard:switchboard@localhost:5432/switchboard",
        ),
        (
            "local",
            "postgresql+psycopg://switchboard:switchboard@db.example.com:5432/switchboard",
        ),
        (
            "local",
            "postgresql+psycopg://switchboard:switchboard@localhost:5432/customer_data",
        ),
        (
            "ci",
            "postgresql+psycopg://switchboard:switchboard@postgres-test:5432/switchboard_test",
        ),
    ],
)
def test_reset_guard_rejects_unsafe_environment_host_or_database(
    environment: str,
    database_url: str,
) -> None:
    with pytest.raises(UnsafeDemoResetError):
        require_safe_demo_reset(settings(environment=environment, database_url=database_url))


def test_sequence_id_generator_is_finite_and_ordered() -> None:
    generator = SequenceIdGenerator(("first", "second"))

    assert generator.new() == "first"
    assert generator.new() == "second"
    with pytest.raises(RuntimeError, match="exhausted"):
        generator.new()


def test_sequence_id_generator_rejects_empty_sequence() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        SequenceIdGenerator[object](())
