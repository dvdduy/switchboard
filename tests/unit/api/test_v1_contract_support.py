from datetime import UTC
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import (
    build_conversation_api_services,
    conversation_url,
    require_idempotency_key,
    require_team_id,
    turn_events_url,
)
from switchboard.adapters.api.v1_models import (
    MAX_MESSAGE_CONTENT_LENGTH,
    V1AcceptedTurnResponse,
    V1ContinueConversationRequest,
)
from switchboard.adapters.system import SystemClock, UuidGenerator
from switchboard.application.errors import (
    ConversationClosedError,
    ConversationTeamMismatchError,
    IdempotencyConflictError,
)
from switchboard.application.services.readiness import ReadinessService
from switchboard.bootstrap.config import Settings
from switchboard.domain.identifiers import ConversationId, TeamId, TurnId
from switchboard.domain.turns import TurnStatus


class UnusedUnitOfWorkFactory:
    def __call__(self):
        raise AssertionError("composition must not open a unit of work")


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": "postgresql+psycopg://unused/unused",
            "redis_url": "redis://unused/0",
        }
    )


def test_message_request_preserves_exact_content_but_rejects_blank_and_oversized() -> None:
    request = V1ContinueConversationRequest(user_message="  exact content  ")
    assert request.user_message == "  exact content  "

    with pytest.raises(ValidationError):
        V1ContinueConversationRequest(user_message="   ")
    with pytest.raises(ValidationError):
        V1ContinueConversationRequest(user_message="x" * (MAX_MESSAGE_CONTENT_LENGTH + 1))
    with pytest.raises(ValidationError):
        V1ContinueConversationRequest.model_validate(
            {"user_message": "valid", "unexpected": "field"}
        )


def test_response_model_serializes_stable_enum_values_and_relative_links() -> None:
    conversation_id = ConversationId(uuid4())
    turn_id = TurnId(uuid4())
    response = V1AcceptedTurnResponse(
        conversation_id=conversation_id,
        message_id=uuid4(),
        turn_id=turn_id,
        status=TurnStatus.RECEIVED,
        conversation_url=conversation_url(conversation_id),
        events_url=turn_events_url(turn_id),
    )

    payload = response.model_dump(mode="json")

    assert payload["status"] == "received"
    assert payload["conversation_url"] == f"/api/v1/conversations/{conversation_id}"
    assert payload["events_url"] == f"/api/v1/turns/{turn_id}/events"


def test_system_adapters_produce_utc_time_and_distinct_typed_ids() -> None:
    now = SystemClock().now()
    generator = UuidGenerator(ConversationId)

    first = generator.new()
    second = generator.new()

    assert now.tzinfo is UTC
    assert isinstance(first, UUID)
    assert first != second


def test_service_bundle_composes_without_opening_persistence() -> None:
    services = build_conversation_api_services(UnusedUnitOfWorkFactory())

    assert services.start_conversation is not None
    assert services.continue_conversation is not None
    assert services.get_conversation is not None
    assert services.list_messages is not None
    assert services.get_turn is not None


async def test_header_dependencies_preserve_valid_values_and_reject_invalid_keys() -> None:
    team_id = TeamId(uuid4())
    assert require_team_id(team_id) == team_id
    assert require_idempotency_key("Exact-Key_1") == "Exact-Key_1"

    with pytest.raises(Exception, match="idempotency key"):
        require_idempotency_key("contains space")


async def test_validation_errors_are_stable_and_do_not_echo_rejected_content() -> None:
    app = create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
    )

    @app.post("/probe")
    async def probe(request: V1ContinueConversationRequest) -> dict[str, str]:
        return {"message": request.user_message}

    sensitive = "do-not-echo-" + "x" * MAX_MESSAGE_CONTENT_LENGTH
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/probe", json={"user_message": sensitive})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request is invalid.",
            "details": [{"field": "body.user_message", "code": "too_long"}],
        }
    }
    assert sensitive not in response.text


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code"),
    [
        ("/conflict", 409, "idempotency_conflict"),
        ("/closed", 409, "conversation_closed"),
        ("/private", 404, "resource_not_found"),
    ],
)
async def test_application_errors_map_without_internal_details(
    path: str,
    expected_status: int,
    expected_code: str,
) -> None:
    app = create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
    )

    @app.get("/conflict")
    async def conflict() -> None:
        raise IdempotencyConflictError("sensitive conflict detail")

    @app.get("/closed")
    async def closed() -> None:
        raise ConversationClosedError("sensitive lifecycle detail")

    @app.get("/private")
    async def private(
        team_id: Annotated[TeamId, Depends(require_team_id)],
    ) -> None:
        raise ConversationTeamMismatchError(f"private owner for {team_id}")

    transport = ASGITransport(app=app)
    headers = {"X-Team-ID": str(uuid4())} if path == "/private" else {}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers=headers)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert "sensitive" not in response.text
    assert "private owner" not in response.text
