"""External-client contract coverage for the Day 6 conversation API."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import build_conversation_api_services
from switchboard.adapters.persistence.schema import conversations
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.system import SystemClock, UuidGenerator
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.application.use_cases.simulate_assistant_response import (
    SimulateAssistantResponse,
    SimulateAssistantResponseCommand,
)
from switchboard.bootstrap.config import Settings
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import ContextPolicy
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ExecutionEventId,
    MessageId,
    TeamId,
    TurnId,
)

NOW = datetime(2026, 7, 14, 22, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class UnexpectedSleeper:
    async def sleep(self, delay_seconds: float) -> None:
        raise AssertionError(f"terminal stream unexpectedly polled after {delay_seconds}")


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": "postgresql+psycopg://unused/unused",
            "redis_url": "redis://unused/0",
        }
    )


def make_app(unit_of_work_factory: SqlAlchemyUnitOfWorkFactory):
    return create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
        conversation_api_services=build_conversation_api_services(unit_of_work_factory),
        replay_turn_events=ReplayTurnEvents(
            unit_of_work_factory=unit_of_work_factory,
            sleeper=UnexpectedSleeper(),
        ),
    )


async def seed_agent(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
) -> AgentVersion:
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="External Client Assistant",
        created_at=NOW,
    )
    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=definition.id,
        version_number=1,
        context_policy=ContextPolicy(4096, 512, 256, 256, 1),
        created_at=NOW,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(definition)
        await unit_of_work.agents.add_version(version)
        await unit_of_work.commit()
    return version


def command_headers(team_id: TeamId, key: str) -> dict[str, str]:
    return {"X-Team-ID": str(team_id), "Idempotency-Key": key}


async def test_external_client_can_replay_continue_page_inspect_and_stream(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    agent = await seed_agent(unit_of_work_factory, team_id=team_id)
    transport = ASGITransport(app=make_app(unit_of_work_factory))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_request = {
            "agent_version_id": str(agent.id),
            "initial_user_message": "First external request.",
        }
        created = await client.post(
            "/api/v1/conversations",
            headers=command_headers(team_id, "create-001"),
            json=create_request,
        )
        replayed = await client.post(
            "/api/v1/conversations",
            headers=command_headers(team_id, "create-001"),
            json=create_request,
        )

        assert created.status_code == 202
        assert replayed.status_code == 202
        assert replayed.json() == created.json()
        accepted = created.json()

        continued = await client.post(
            f"{accepted['conversation_url']}/turns",
            headers=command_headers(team_id, "continue-001"),
            json={"user_message": "Second external request."},
        )
        assert continued.status_code == 202

        conversation = await client.get(
            accepted["conversation_url"],
            headers={"X-Team-ID": str(team_id)},
        )
        first_page = await client.get(
            f"{accepted['conversation_url']}/messages",
            headers={"X-Team-ID": str(team_id)},
            params={"limit": 1},
        )
        second_page = await client.get(
            f"{accepted['conversation_url']}/messages",
            headers={"X-Team-ID": str(team_id)},
            params={"after_sequence": 1, "limit": 1},
        )
        turn = await client.get(
            f"/api/v1/turns/{accepted['turn_id']}",
            headers={"X-Team-ID": str(team_id)},
        )

        assert conversation.status_code == 200
        assert conversation.json()["status"] == "active"
        assert first_page.json()["items"][0]["sequence"] == 1
        assert first_page.json()["has_more"] is True
        assert second_page.json()["items"][0]["sequence"] == 2
        assert second_page.json()["has_more"] is False
        assert turn.status_code == 200
        assert turn.json()["status"] == "received"
        assert turn.json()["events_url"] == accepted["events_url"]

        turn_id = TurnId(UUID(accepted["turn_id"]))
        async with unit_of_work_factory() as unit_of_work:
            attempts = await unit_of_work.turns.list_attempts(turn_id)
        assert len(attempts) == 1

        simulator = SimulateAssistantResponse(
            unit_of_work_factory=unit_of_work_factory,
            clock=SystemClock(),
            event_ids=UuidGenerator(ExecutionEventId),
            message_ids=UuidGenerator(MessageId),
        )
        await simulator.execute(
            SimulateAssistantResponseCommand(
                turn_id=turn_id,
                attempt_id=attempts[0].id,
                response_text="Durable external response.",
            )
        )

        stream = await client.get(
            accepted["events_url"],
            headers={"X-Team-ID": str(team_id)},
        )

    assert stream.status_code == 200
    assert stream.headers["content-type"] == "text/event-stream"
    frames = [frame for frame in stream.text.split("\n\n") if frame]
    sequences = [int(frame.splitlines()[0].removeprefix("id: ")) for frame in frames]
    assert sequences == list(range(1, len(frames) + 1))
    assert frames[0].splitlines()[1] == "event: turn.started"
    assert frames[-1].splitlines()[1] == "event: turn.completed"


async def test_concurrent_duplicate_create_has_one_public_result_and_graph(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    agent = await seed_agent(unit_of_work_factory, team_id=team_id)
    transport = ASGITransport(app=make_app(unit_of_work_factory))
    request = {
        "agent_version_id": str(agent.id),
        "initial_user_message": "Only create this graph once.",
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post(
                "/api/v1/conversations",
                headers=command_headers(team_id, "concurrent-create"),
                json=request,
            ),
            client.post(
                "/api/v1/conversations",
                headers=command_headers(team_id, "concurrent-create"),
                json=request,
            ),
        )

        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        conversation_id = first.json()["conversation_id"]
        messages = await client.get(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"X-Team-ID": str(team_id)},
        )

    assert [item["sequence"] for item in messages.json()["items"]] == [1]


async def test_conflicts_closed_conversations_and_unknown_agents_are_stable(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    database_engine: AsyncEngine,
) -> None:
    team_id = TeamId(uuid4())
    agent = await seed_agent(unit_of_work_factory, team_id=team_id)
    transport = ASGITransport(app=make_app(unit_of_work_factory))
    headers = command_headers(team_id, "stable-create")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "agent_version_id": str(agent.id),
                "initial_user_message": "Original request.",
            },
        )
        conflict = await client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "agent_version_id": str(agent.id),
                "initial_user_message": "Changed request.",
            },
        )
        unknown_agent = await client.post(
            "/api/v1/conversations",
            headers=command_headers(team_id, "unknown-agent"),
            json={
                "agent_version_id": str(uuid4()),
                "initial_user_message": "Cannot be accepted.",
            },
        )

        conversation_id = UUID(created.json()["conversation_id"])
        async with database_engine.begin() as connection:
            await connection.execute(
                update(conversations)
                .where(conversations.c.id == conversation_id)
                .values(status="closed")
            )

        closed = await client.post(
            f"/api/v1/conversations/{conversation_id}/turns",
            headers=command_headers(team_id, "closed-continuation"),
            json={"user_message": "Must roll back."},
        )
        messages = await client.get(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"X-Team-ID": str(team_id)},
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert unknown_agent.status_code == 404
    assert unknown_agent.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "The requested resource was not found.",
        }
    }
    assert closed.status_code == 409
    assert closed.json()["error"]["code"] == "conversation_closed"
    assert [item["content"] for item in messages.json()["items"]] == ["Original request."]


async def test_cross_team_access_matches_unknown_resource_response(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    owner_team = TeamId(uuid4())
    other_team = TeamId(uuid4())
    agent = await seed_agent(unit_of_work_factory, team_id=owner_team)
    transport = ASGITransport(app=make_app(unit_of_work_factory))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/conversations",
            headers=command_headers(owner_team, "owner-create"),
            json={
                "agent_version_id": str(agent.id),
                "initial_user_message": "Owner data.",
            },
        )
        conversation_id = created.json()["conversation_id"]
        cross_team = await client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers={"X-Team-ID": str(other_team)},
        )
        unknown = await client.get(
            f"/api/v1/conversations/{uuid4()}",
            headers={"X-Team-ID": str(other_team)},
        )
        cross_team_command = await client.post(
            f"/api/v1/conversations/{conversation_id}/turns",
            headers=command_headers(other_team, "cross-team-command"),
            json={"user_message": "Not allowed."},
        )

    assert cross_team.status_code == unknown.status_code == 404
    assert cross_team.json() == unknown.json()
    assert cross_team_command.status_code == 404
    assert cross_team_command.json() == unknown.json()


async def test_invalid_contract_inputs_are_sanitized_and_openapi_is_complete(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    transport = ASGITransport(app=make_app(unit_of_work_factory))
    rejected_content = "private-marker-" + ("x" * 32_000)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        malformed_id = await client.get(
            "/api/v1/conversations/not-a-uuid",
            headers={"X-Team-ID": str(team_id)},
        )
        malformed_body = await client.post(
            "/api/v1/conversations",
            headers=command_headers(team_id, "malformed-body"),
            json={"agent_version_id": str(uuid4()), "unexpected": True},
        )
        oversized = await client.post(
            "/api/v1/conversations",
            headers=command_headers(team_id, "oversized-body"),
            json={
                "agent_version_id": str(uuid4()),
                "initial_user_message": rejected_content,
            },
        )
        missing_headers = await client.post(
            "/api/v1/conversations",
            json={
                "agent_version_id": str(uuid4()),
                "initial_user_message": "Missing headers.",
            },
        )
        bad_page = await client.get(
            f"/api/v1/conversations/{uuid4()}/messages",
            headers={"X-Team-ID": str(team_id)},
            params={"after_sequence": -1, "limit": 101},
        )
        openapi = await client.get("/openapi.json")

    for response in (malformed_id, malformed_body, oversized, bad_page):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
    assert rejected_content not in oversized.text
    assert missing_headers.status_code == 400
    assert missing_headers.json()["error"]["code"] == "invalid_header"

    schema = openapi.json()
    assert {
        "/api/v1/conversations",
        "/api/v1/conversations/{conversation_id}/turns",
        "/api/v1/conversations/{conversation_id}",
        "/api/v1/conversations/{conversation_id}/messages",
        "/api/v1/turns/{turn_id}",
        "/api/v1/turns/{turn_id}/events",
    } <= schema["paths"].keys()
    assert schema["paths"]["/api/v1/conversations"]["post"]["responses"].keys() >= {
        "202",
        "400",
        "404",
        "409",
        "422",
    }
