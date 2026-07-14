"""Stable sanitized error handling for the versioned public API."""

from dataclasses import dataclass
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from switchboard.adapters.api.v1_models import (
    V1ErrorDetail,
    V1ErrorResponse,
    V1ValidationIssue,
)
from switchboard.application.errors import (
    AgentDefinitionNotFoundError,
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
    ApplicationError,
    ApprovalDecisionConflictError,
    ApprovalLifecycleConflictError,
    ApprovalNotFoundError,
    ApprovalRevalidationError,
    ApprovalTeamMismatchError,
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationTeamMismatchError,
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    PaginationValidationError,
    TurnNotFoundError,
    TurnTeamMismatchError,
)
from switchboard.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class V1ApiError(Exception):
    """Transport failure with an intentional stable public representation."""

    status_code: int
    code: str
    message: str


def install_v1_error_handlers(app: FastAPI) -> None:
    """Install sanitized handlers shared by all v1 endpoints."""

    app.add_exception_handler(V1ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(ApplicationError, _handle_application_error)
    app.add_exception_handler(DomainValidationError, _handle_domain_validation)


async def _handle_api_error(_: Request, error: Exception) -> JSONResponse:
    problem = cast(V1ApiError, error)
    return _error_response(problem.status_code, problem.code, problem.message)


async def _handle_request_validation(_: Request, error: Exception) -> JSONResponse:
    validation_error = cast(RequestValidationError, error)
    raw_errors = validation_error.errors()
    is_header_error = bool(raw_errors) and all(
        item.get("loc", (None,))[0] == "header" for item in raw_errors
    )
    code = "invalid_header" if is_header_error else "invalid_request"
    message = (
        "A required header is missing or invalid." if is_header_error else "The request is invalid."
    )
    details = tuple(
        V1ValidationIssue(
            field=".".join(str(part) for part in item.get("loc", ())),
            code=_validation_issue_code(str(item.get("type", "invalid"))),
        )
        for item in raw_errors
    )
    return _error_response(
        status.HTTP_400_BAD_REQUEST if is_header_error else status.HTTP_422_UNPROCESSABLE_CONTENT,
        code,
        message,
        details=details,
    )


async def _handle_application_error(_: Request, error: Exception) -> JSONResponse:
    application_error = cast(ApplicationError, error)
    if isinstance(application_error, InvalidIdempotencyKeyError):
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "invalid_header",
            "A required header is missing or invalid.",
        )
    if isinstance(application_error, IdempotencyConflictError):
        return _error_response(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "The idempotency key was already used for a different request.",
        )
    if isinstance(application_error, ConversationClosedError):
        return _error_response(
            status.HTTP_409_CONFLICT,
            "conversation_closed",
            "The conversation is closed.",
        )
    if isinstance(
        application_error,
        (
            ApprovalDecisionConflictError,
            ApprovalLifecycleConflictError,
            ApprovalRevalidationError,
        ),
    ):
        return _error_response(
            status.HTTP_409_CONFLICT,
            "approval_conflict",
            "The approval can no longer authorize this action.",
        )
    if isinstance(
        application_error,
        (
            AgentDefinitionNotFoundError,
            AgentTeamMismatchError,
            AgentVersionNotFoundError,
            ApprovalNotFoundError,
            ApprovalTeamMismatchError,
            ConversationNotFoundError,
            ConversationTeamMismatchError,
            TurnNotFoundError,
            TurnTeamMismatchError,
        ),
    ):
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "resource_not_found",
            "The requested resource was not found.",
        )
    if isinstance(application_error, PaginationValidationError):
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_request",
            "The request is invalid.",
        )
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "The request could not be completed.",
    )


async def _handle_domain_validation(_: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "invalid_request",
        "The request is invalid.",
    )


def _validation_issue_code(error_type: str) -> str:
    if error_type == "missing":
        return "missing"
    if error_type in {"string_too_long", "bytes_too_long"}:
        return "too_long"
    if error_type == "extra_forbidden":
        return "unexpected_field"
    if error_type == "uuid_parsing":
        return "invalid_uuid"
    return "invalid"


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: tuple[V1ValidationIssue, ...] = (),
) -> JSONResponse:
    model = V1ErrorResponse(
        error=V1ErrorDetail(
            code=code,
            message=message,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=model.model_dump(mode="json", exclude_defaults=True),
    )
