from pathlib import Path

import pytest
from pydantic import ValidationError

from switchboard.bootstrap.config import Settings, load_settings


def make_settings(
    *,
    database_url: str = ("postgresql+psycopg://user:password@localhost:5432/switchboard"),
    redis_url: str = "redis://localhost:6379/0",
) -> Settings:
    """Create settings from explicit values without reading environment sources."""

    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": database_url,
            "redis_url": redis_url,
            "log_level": "DEBUG",
        }
    )


def test_settings_accept_valid_explicit_configuration() -> None:
    settings = make_settings()

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"


def test_settings_require_database_and_redis_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SWITCHBOARD_DATABASE_URL", raising=False)
    monkeypatch.delenv("SWITCHBOARD_REDIS_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings.model_validate({})


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///switchboard.db",
        "mysql://localhost/switchboard",
        "not-a-url",
    ],
)
def test_settings_reject_non_postgresql_database_urls(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url=database_url)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost/switchboard",
        "sqlite:///switchboard.db",
        "mysql://localhost/switchboard",
        "not-a-url",
    ],
)
def test_settings_reject_non_psycopg_database_urls(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url=database_url)


@pytest.mark.parametrize(
    "redis_url",
    [
        "http://localhost:6379",
        "memory://cache",
        "not-a-url",
    ],
)
def test_settings_reject_invalid_redis_urls(redis_url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(redis_url=redis_url)


def test_load_settings_reads_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Ensure no repository .env file participates in this test.
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("SWITCHBOARD_ENVIRONMENT", "test")
    monkeypatch.setenv(
        "SWITCHBOARD_DATABASE_URL",
        "postgresql+psycopg://user:password@localhost:5432/switchboard_test",
    )
    monkeypatch.setenv(
        "SWITCHBOARD_REDIS_URL",
        "redis://localhost:6379/15",
    )
    monkeypatch.setenv("SWITCHBOARD_LOG_LEVEL", "WARNING")

    settings = load_settings()

    assert settings.environment == "test"
    assert settings.database_url.endswith("/switchboard_test")
    assert settings.redis_url == "redis://localhost:6379/15"
    assert settings.log_level == "WARNING"


def test_load_settings_fails_when_required_environment_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Pydantic looks for the configured dotenv file in the current working
    # directory, so changing to this empty directory isolates the test.
    monkeypatch.chdir(tmp_path)

    monkeypatch.delenv("SWITCHBOARD_DATABASE_URL", raising=False)
    monkeypatch.delenv("SWITCHBOARD_REDIS_URL", raising=False)

    with pytest.raises(ValidationError):
        load_settings()
