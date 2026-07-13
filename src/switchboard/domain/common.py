"""Shared domain validation helpers."""

from datetime import UTC, datetime

from switchboard.domain.errors import DomainValidationError


def normalize_utc(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    """Require a timezone-aware datetime and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")

    return value.astimezone(UTC)


def require_not_blank(
    value: str,
    *,
    field_name: str,
) -> str:
    """Return a trimmed non-blank string."""

    normalized = value.strip()

    if not normalized:
        raise DomainValidationError(f"{field_name} must not be blank")

    return normalized


def require_positive(
    value: int,
    *,
    field_name: str,
) -> None:
    """Require a positive integer."""

    if value <= 0:
        raise DomainValidationError(f"{field_name} must be greater than zero")


def require_not_before(
    value: datetime,
    *,
    minimum: datetime,
    field_name: str,
    minimum_field_name: str,
) -> None:
    """Require one datetime not to precede another."""

    if value < minimum:
        raise DomainValidationError(f"{field_name} must not be before {minimum_field_name}")
