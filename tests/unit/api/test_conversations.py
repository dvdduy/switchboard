from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import ConversationApiServices
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.use_cases.continue_conversation import ContinueConversationResult
from switchboard.application.use_cases.read_conversations import (
    ConversationReadModel,
    MessagePage,
    MessageReadModel,
    TurnAttemptReadModel,
    TurnReadModel,
)
from switchboard.application.use_cases.start_conversation import StartConversationResult
from switchboard.bootstrap.config import Settings
from switchboard.domain.conversations import ConversationStatus, MessageRole
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus

NOW = datetime(2026, 7, 14, 21, 0, tzinfo=UTC)


class CapturingService:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def execute(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        return self.result


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": "postgresql+psycopg://unused/unused",
            "redis_url": "redis://unused/0",
        }
    )


def make_services():
    conversation_id = ConversationId(uuid4())
    message_id = MessageId(uuid4())
    turn_id = TurnId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    accepted = StartConversationResult(
        conversation_id=conversation_id,
        message_id=message_id,
        turn_id=turn_id,
        attempt_id=TurnAttemptId(uuid4()),
    )
    start = CapturingService(accepted)
    continuation = CapturingService(
        ContinueConversationResult(
            conversation_id=conversation_id,
            message_id=message_id,
            turn_id=turn_id,
            attempt_id=TurnAttemptId(uuid4()),
        )
    )
    get_conversation = CapturingService(
        ConversationReadModel(
            conversation_id=conversation_id,
            default_agent_version_id=agent_version_id,
            status=ConversationStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    list_messages = CapturingService(
        MessagePage(
            items=(
                MessageReadModel(
                    message_id=message_id,
                    sequence=2,
                    role=MessageRole.USER,
                    content="Continue",
                    created_at=NOW,
                ),
            ),
            next_after_sequence=2,
            has_more=False,
        )
    )
    get_turn = CapturingService(
        TurnReadModel(
            turn_id=turn_id,
            conversation_id=conversation_id,
            input_message_id=message_id,
            agent_version_id=agent_version_id,
            status=TurnStatus.RECEIVED,
            created_at=NOW,
            completed_at=None,
            attempts=(
                TurnAttemptReadModel(
                    attempt_number=1,
                    status=TurnAttemptStatus.PENDING,
                    created_at=NOW,
                    started_at=None,
                    completed_at=None,
                ),
            ),
        )
    )
    services = ConversationApiServices(
        start_conversation=start,
        continue_conversation=continuation,
        get_conversation=get_conversation,
        list_messages=list_messages,
        get_turn=get_turn,
    )
    return services, (start, continuation, get_conversation, list_messages, get_turn)


def make_app():
    services, captures = make_services()
    app = create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
        conversation_api_services=services,
    )
    return app, captures


async def test_create_and_continue_return_consistent_accepted_links() -> None:
    app, captures = make_app()
    start, continuation, _, _, _ = captures
    team_id = TeamId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    headers = {"X-Team-ID": str(team_id), "Idempotency-Key": "command-001"}
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "agent_version_id": str(agent_version_id),
                "initial_user_message": "Start",
            },
        )
        conversation_id = created.json()["conversation_id"]
        continued = await client.post(
            f"/api/v1/conversations/{conversation_id}/turns",
            headers=headers,
            json={"user_message": "Continue"},
        )

    assert created.status_code == 202
    assert continued.status_code == 202
    for response in (created, continued):
        payload = response.json()
        assert payload["status"] == "received"
        assert payload["conversation_url"] == f"/api/v1/conversations/{conversation_id}"
        assert payload["events_url"] == f"/api/v1/turns/{payload['turn_id']}/events"
    start_command = start.calls[0][0][0]
    continue_command = continuation.calls[0][0][0]
    assert start_command.team_id == team_id
    assert start_command.agent_version_id == agent_version_id
    assert start_command.idempotency_key == "command-001"
    assert continue_command.conversation_id == ConversationId(UUID(conversation_id))
    assert continue_command.user_message == "Continue"


async def test_read_routes_map_safe_models_and_pagination() -> None:
    app, captures = make_app()
    _, _, get_conversation, list_messages, get_turn = captures
    conversation = get_conversation.result
    turn = get_turn.result
    team_id = TeamId(uuid4())
    headers = {"X-Team-ID": str(team_id)}
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        conversation_response = await client.get(
            f"/api/v1/conversations/{conversation.conversation_id}",
            headers=headers,
        )
        messages_response = await client.get(
            f"/api/v1/conversations/{conversation.conversation_id}/messages",
            headers=headers,
            params={"after_sequence": 1, "limit": 2},
        )
        turn_response = await client.get(
            f"/api/v1/turns/{turn.turn_id}",
            headers=headers,
        )

    assert conversation_response.status_code == 200
    assert conversation_response.json()["status"] == "active"
    assert messages_response.json() == {
        "items": [
            {
                "message_id": str(list_messages.result.items[0].message_id),
                "sequence": 2,
                "role": "user",
                "content": "Continue",
                "created_at": "2026-07-14T21:00:00Z",
            }
        ],
        "next_after_sequence": 2,
        "has_more": False,
    }
    turn_payload = turn_response.json()
    assert turn_payload["attempts"] == [
        {
            "attempt_number": 1,
            "status": "pending",
            "created_at": "2026-07-14T21:00:00Z",
            "started_at": None,
            "completed_at": None,
        }
    ]
    assert "attempt_id" not in turn_payload["attempts"][0]
    assert "failure_code" not in turn_payload["attempts"][0]
    assert turn_payload["events_url"] == f"/api/v1/turns/{turn.turn_id}/events"
    assert list_messages.calls[0][1]["after_sequence"] == 1
    assert list_messages.calls[0][1]["limit"] == 2


async def test_route_validation_prevents_service_delegation() -> None:
    app, captures = make_app()
    start, _, _, list_messages, _ = captures
    team_id = TeamId(uuid4())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing_key = await client.post(
            "/api/v1/conversations",
            headers={"X-Team-ID": str(team_id)},
            json={"agent_version_id": str(uuid4()), "initial_user_message": "Start"},
        )
        bad_page = await client.get(
            f"/api/v1/conversations/{uuid4()}/messages",
            headers={"X-Team-ID": str(team_id)},
            params={"after_sequence": -1, "limit": 101},
        )

    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "invalid_header"
    assert bad_page.status_code == 422
    assert bad_page.json()["error"]["code"] == "invalid_request"
    assert start.calls == []
    assert list_messages.calls == []


def test_openapi_documents_all_v1_paths_models_and_examples() -> None:
    app, _ = make_app()
    schema = app.openapi()
    paths = schema["paths"]

    assert {
        "/api/v1/conversations",
        "/api/v1/conversations/{conversation_id}/turns",
        "/api/v1/conversations/{conversation_id}",
        "/api/v1/conversations/{conversation_id}/messages",
        "/api/v1/turns/{turn_id}",
    } <= paths.keys()
    components = schema["components"]["schemas"]
    assert {
        "V1AcceptedTurnResponse",
        "V1ConversationResponse",
        "V1CreateConversationRequest",
        "V1ContinueConversationRequest",
        "V1ErrorResponse",
        "V1MessagePageResponse",
        "V1TurnResponse",
    } <= components.keys()
    create_operation = paths["/api/v1/conversations"]["post"]
    request_examples = create_operation["requestBody"]["content"]["application/json"]["examples"]
    accepted_example = create_operation["responses"]["202"]["content"]["application/json"][
        "example"
    ]
    assert request_examples["create"]["value"]["initial_user_message"]
    assert accepted_example["status"] == "received"
