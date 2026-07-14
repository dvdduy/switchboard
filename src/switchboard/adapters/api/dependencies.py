"""FastAPI boundary dependencies and conversation-service composition."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Header

from switchboard.adapters.system import SystemClock, UuidGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.tool_adapter import ToolAdapterResolver
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.application.services.command_idempotency import hash_idempotency_key
from switchboard.application.use_cases.continue_conversation import ContinueConversation
from switchboard.application.use_cases.manage_approvals import ManageApprovals
from switchboard.application.use_cases.read_conversations import (
    GetConversation,
    GetTurn,
    ListConversationMessages,
)
from switchboard.application.use_cases.start_conversation import StartConversation
from switchboard.domain.identifiers import (
    ActorId,
    CommandReceiptId,
    ConversationId,
    ExecutionEventId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)


@dataclass(frozen=True, slots=True)
class ConversationApiServices:
    """Application services consumed by the v1 conversation transport."""

    start_conversation: StartConversation
    continue_conversation: ContinueConversation
    get_conversation: GetConversation
    list_messages: ListConversationMessages
    get_turn: GetTurn


@dataclass(frozen=True, slots=True)
class ApprovalApiServices:
    """Application services consumed by the v1 approval transport."""

    manage: ManageApprovals


def build_approval_api_services(
    unit_of_work_factory: UnitOfWorkFactory,
    *,
    adapter_resolver: ToolAdapterResolver,
    schema_validator: JsonSchemaValidator,
) -> ApprovalApiServices:
    clock = SystemClock()
    return ApprovalApiServices(
        manage=ManageApprovals(
            unit_of_work_factory=unit_of_work_factory,
            adapter_resolver=adapter_resolver,
            schema_validator=schema_validator,
            clock=clock,
            receipt_ids=UuidGenerator(CommandReceiptId),
            event_ids=UuidGenerator(ExecutionEventId),
        )
    )


def build_conversation_api_services(
    unit_of_work_factory: UnitOfWorkFactory,
) -> ConversationApiServices:
    """Compose conversation use cases from infrastructure-level dependencies."""

    clock = SystemClock()
    return ConversationApiServices(
        start_conversation=StartConversation(
            unit_of_work_factory=unit_of_work_factory,
            clock=clock,
            conversation_ids=UuidGenerator(ConversationId),
            message_ids=UuidGenerator(MessageId),
            turn_ids=UuidGenerator(TurnId),
            attempt_ids=UuidGenerator(TurnAttemptId),
            receipt_ids=UuidGenerator(CommandReceiptId),
        ),
        continue_conversation=ContinueConversation(
            unit_of_work_factory=unit_of_work_factory,
            clock=clock,
            message_ids=UuidGenerator(MessageId),
            turn_ids=UuidGenerator(TurnId),
            attempt_ids=UuidGenerator(TurnAttemptId),
            receipt_ids=UuidGenerator(CommandReceiptId),
        ),
        get_conversation=GetConversation(unit_of_work_factory=unit_of_work_factory),
        list_messages=ListConversationMessages(unit_of_work_factory=unit_of_work_factory),
        get_turn=GetTurn(unit_of_work_factory=unit_of_work_factory),
    )


def require_team_id(
    x_team_id: Annotated[
        UUID,
        Header(
            alias="X-Team-ID",
            description="Development-only team identity; not production authentication.",
            examples=["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        ),
    ],
) -> TeamId:
    """Convert explicit development team context to a domain identity."""

    return TeamId(x_team_id)


def require_idempotency_key(
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            description="Opaque 1-128 character command replay key.",
            examples=["client-command-001"],
        ),
    ],
) -> str:
    """Validate a command key while preserving its exact opaque value."""

    hash_idempotency_key(idempotency_key)
    return idempotency_key


def require_actor_id(
    x_actor_id: Annotated[
        UUID,
        Header(
            alias="X-Actor-ID",
            description="Development-only actor identity; not production authentication.",
            examples=["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
        ),
    ],
) -> ActorId:
    """Convert explicit development actor context to a domain identity."""

    return ActorId(x_actor_id)


def conversation_url(conversation_id: ConversationId) -> str:
    return f"/api/v1/conversations/{conversation_id}"


def turn_events_url(turn_id: TurnId) -> str:
    return f"/api/v1/turns/{turn_id}/events"
