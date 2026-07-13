"""Application configuration loaded at the composition root."""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration for Switchboard."""

    model_config = SettingsConfigDict(
        env_prefix="SWITCHBOARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "test", "ci", "production"] = "local"
    database_url: str = Field(min_length=1)
    redis_url: str = Field(min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        allowed_prefixes = (
            "postgresql://",
            "postgresql+psycopg://",
        )

        if not value.startswith(allowed_prefixes):
            raise ValueError("database_url must use PostgreSQL with a psycopg-compatible scheme")

        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("redis_url must use the redis or rediss scheme")

        return value


def load_settings() -> Settings:
    """Load and validate settings from environment variables and `.env`."""

    return Settings()
