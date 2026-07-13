"""Translation between relational rows and pure domain entities."""

from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import (
    Turn,
    TurnAttempt,
    TurnAttemptStatus,
    TurnStatus,
)


class Record(Protocol):
    """Record supporting lookup by relational column name."""

    def __getitem__(self, key: str, /) -> object: ...


def agent_definition_to_record(
    definition: AgentDefinition,
) -> dict[str, object]:
    return {
        "id": definition.id,
        "team_id": definition.team_id,
        "name": definition.name,
        "created_at": definition.created_at,
    }


def agent_definition_from_record(
    record: Record,
) -> AgentDefinition:
    return AgentDefinition(
        id=AgentDefinitionId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        name=cast(str, record["name"]),
        created_at=cast(datetime, record["created_at"]),
    )


def agent_version_to_record(
    version: AgentVersion,
) -> dict[str, object]:
    return {
        "id": version.id,
        "agent_definition_id": version.agent_definition_id,
        "version_number": version.version_number,
        "created_at": version.created_at,
    }


def agent_version_from_record(
    record: Record,
) -> AgentVersion:
    return AgentVersion(
        id=AgentVersionId(cast(UUID, record["id"])),
        agent_definition_id=AgentDefinitionId(cast(UUID, record["agent_definition_id"])),
        version_number=cast(int, record["version_number"]),
        created_at=cast(datetime, record["created_at"]),
    )


def conversation_to_record(
    conversation: Conversation,
) -> dict[str, object]:
    return {
        "id": conversation.id,
        "team_id": conversation.team_id,
        "default_agent_version_id": conversation.default_agent_version_id,
        "status": conversation.status.value,
        "next_message_sequence": conversation.next_message_sequence,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def conversation_from_record(
    record: Record,
) -> Conversation:
    return Conversation(
        id=ConversationId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        default_agent_version_id=AgentVersionId(cast(UUID, record["default_agent_version_id"])),
        status=ConversationStatus(cast(str, record["status"])),
        next_message_sequence=cast(
            int,
            record["next_message_sequence"],
        ),
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


def message_to_record(
    message: Message,
) -> dict[str, object]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sequence": message.sequence,
        "role": message.role.value,
        "content": message.content,
        "created_at": message.created_at,
    }


def message_from_record(
    record: Record,
) -> Message:
    return Message(
        id=MessageId(cast(UUID, record["id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        sequence=cast(int, record["sequence"]),
        role=MessageRole(cast(str, record["role"])),
        content=cast(str, record["content"]),
        created_at=cast(datetime, record["created_at"]),
    )


def turn_to_record(
    turn: Turn,
) -> dict[str, object]:
    return {
        "id": turn.id,
        "conversation_id": turn.conversation_id,
        "input_message_id": turn.input_message_id,
        "agent_version_id": turn.agent_version_id,
        "status": turn.status.value,
        "created_at": turn.created_at,
        "completed_at": turn.completed_at,
    }


def turn_from_record(
    record: Record,
) -> Turn:
    return Turn(
        id=TurnId(cast(UUID, record["id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        input_message_id=MessageId(cast(UUID, record["input_message_id"])),
        agent_version_id=AgentVersionId(cast(UUID, record["agent_version_id"])),
        status=TurnStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        completed_at=cast(
            datetime | None,
            record["completed_at"],
        ),
    )


def turn_attempt_to_record(
    attempt: TurnAttempt,
) -> dict[str, object]:
    return {
        "id": attempt.id,
        "turn_id": attempt.turn_id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status.value,
        "created_at": attempt.created_at,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "failure_code": attempt.failure_code,
    }


def turn_attempt_from_record(
    record: Record,
) -> TurnAttempt:
    return TurnAttempt(
        id=TurnAttemptId(cast(UUID, record["id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_number=cast(int, record["attempt_number"]),
        status=TurnAttemptStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast(
            datetime | None,
            record["started_at"],
        ),
        completed_at=cast(
            datetime | None,
            record["completed_at"],
        ),
        failure_code=cast(
            str | None,
            record["failure_code"],
        ),
    )
