"""Versioned public conversation command and read routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status

from switchboard.adapters.api.dependencies import (
    ConversationApiServices,
    conversation_url,
    require_idempotency_key,
    require_team_id,
    turn_events_url,
)
from switchboard.adapters.api.v1_models import (
    V1AcceptedTurnResponse,
    V1ContinueConversationRequest,
    V1ConversationResponse,
    V1CreateConversationRequest,
    V1ErrorResponse,
    V1MessagePageResponse,
    V1MessageResponse,
    V1TurnAttemptResponse,
    V1TurnResponse,
)
from switchboard.application.use_cases.continue_conversation import (
    ContinueConversationCommand,
)
from switchboard.application.use_cases.read_conversations import (
    DEFAULT_MESSAGE_PAGE_LIMIT,
    MAX_MESSAGE_PAGE_LIMIT,
    MessagePage,
    TurnReadModel,
)
from switchboard.application.use_cases.start_conversation import StartConversationCommand
from switchboard.domain.identifiers import AgentVersionId, ConversationId, TeamId, TurnId
from switchboard.domain.turns import TurnStatus

READ_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": V1ErrorResponse, "description": "Missing or invalid required header."},
    404: {"model": V1ErrorResponse, "description": "Resource unavailable to this team."},
    422: {"model": V1ErrorResponse, "description": "Invalid request body, path, or query."},
}
COMMAND_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **READ_ERROR_RESPONSES,
    409: {"model": V1ErrorResponse, "description": "Idempotency or lifecycle conflict."},
}

CREATE_REQUEST_EXAMPLES = {
    "create": {
        "summary": "Create a conversation with its first user turn",
        "value": {
            "agent_version_id": "11111111-1111-4111-8111-111111111111",
            "initial_user_message": "Which Project Alpha tasks are overdue?",
        },
    }
}

CONTINUE_REQUEST_EXAMPLES = {
    "continue": {
        "summary": "Append one user turn",
        "value": {"user_message": "Only include tasks assigned to me."},
    }
}

ACCEPTED_RESPONSE_EXAMPLE = {
    "conversation_id": "22222222-2222-4222-8222-222222222222",
    "message_id": "33333333-3333-4333-8333-333333333333",
    "turn_id": "44444444-4444-4444-8444-444444444444",
    "status": "received",
    "conversation_url": ("/api/v1/conversations/22222222-2222-4222-8222-222222222222"),
    "events_url": ("/api/v1/turns/44444444-4444-4444-8444-444444444444/events"),
}


def create_conversation_router(services: ConversationApiServices) -> APIRouter:
    """Create routes that delegate only to application services."""

    router = APIRouter(prefix="/api/v1", tags=["conversations"])

    @router.post(
        "/conversations",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=V1AcceptedTurnResponse,
        responses={
            **COMMAND_ERROR_RESPONSES,
            202: {
                "description": "Conversation and first turn durably accepted.",
                "content": {"application/json": {"example": ACCEPTED_RESPONSE_EXAMPLE}},
            },
        },
    )
    async def create_conversation(
        request: Annotated[
            V1CreateConversationRequest,
            Body(openapi_examples=CREATE_REQUEST_EXAMPLES),
        ],
        team_id: Annotated[TeamId, Depends(require_team_id)],
        idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    ) -> V1AcceptedTurnResponse:
        result = await services.start_conversation.execute(
            StartConversationCommand(
                team_id=team_id,
                agent_version_id=AgentVersionId(request.agent_version_id),
                initial_user_message=request.initial_user_message,
                idempotency_key=idempotency_key,
            )
        )
        return _accepted_turn_response(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            turn_id=result.turn_id,
        )

    @router.post(
        "/conversations/{conversation_id}/turns",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=V1AcceptedTurnResponse,
        responses={
            **COMMAND_ERROR_RESPONSES,
            202: {
                "description": "User turn durably accepted.",
                "content": {"application/json": {"example": ACCEPTED_RESPONSE_EXAMPLE}},
            },
        },
    )
    async def continue_conversation(
        conversation_id: UUID,
        request: Annotated[
            V1ContinueConversationRequest,
            Body(openapi_examples=CONTINUE_REQUEST_EXAMPLES),
        ],
        team_id: Annotated[TeamId, Depends(require_team_id)],
        idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    ) -> V1AcceptedTurnResponse:
        result = await services.continue_conversation.execute(
            ContinueConversationCommand(
                team_id=team_id,
                conversation_id=ConversationId(conversation_id),
                user_message=request.user_message,
                idempotency_key=idempotency_key,
            )
        )
        return _accepted_turn_response(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            turn_id=result.turn_id,
        )

    @router.get(
        "/conversations/{conversation_id}",
        response_model=V1ConversationResponse,
        responses=READ_ERROR_RESPONSES,
    )
    async def get_conversation(
        conversation_id: UUID,
        team_id: Annotated[TeamId, Depends(require_team_id)],
    ) -> V1ConversationResponse:
        result = await services.get_conversation.execute(
            team_id=team_id,
            conversation_id=ConversationId(conversation_id),
        )
        return V1ConversationResponse(
            conversation_id=result.conversation_id,
            default_agent_version_id=result.default_agent_version_id,
            status=result.status,
            created_at=result.created_at,
            updated_at=result.updated_at,
        )

    @router.get(
        "/conversations/{conversation_id}/messages",
        response_model=V1MessagePageResponse,
        responses=READ_ERROR_RESPONSES,
    )
    async def list_messages(
        conversation_id: UUID,
        team_id: Annotated[TeamId, Depends(require_team_id)],
        after_sequence: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_MESSAGE_PAGE_LIMIT)] = (
            DEFAULT_MESSAGE_PAGE_LIMIT
        ),
    ) -> V1MessagePageResponse:
        result = await services.list_messages.execute(
            team_id=team_id,
            conversation_id=ConversationId(conversation_id),
            after_sequence=after_sequence,
            limit=limit,
        )
        return _message_page_response(result)

    @router.get(
        "/turns/{turn_id}",
        response_model=V1TurnResponse,
        responses=READ_ERROR_RESPONSES,
    )
    async def get_turn(
        turn_id: UUID,
        team_id: Annotated[TeamId, Depends(require_team_id)],
    ) -> V1TurnResponse:
        result = await services.get_turn.execute(
            team_id=team_id,
            turn_id=TurnId(turn_id),
        )
        return _turn_response(result)

    return router


def _accepted_turn_response(
    *,
    conversation_id: ConversationId,
    message_id: UUID,
    turn_id: TurnId,
) -> V1AcceptedTurnResponse:
    return V1AcceptedTurnResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        turn_id=turn_id,
        status=TurnStatus.RECEIVED,
        conversation_url=conversation_url(conversation_id),
        events_url=turn_events_url(turn_id),
    )


def _message_page_response(page: MessagePage) -> V1MessagePageResponse:
    return V1MessagePageResponse(
        items=tuple(
            V1MessageResponse(
                message_id=message.message_id,
                sequence=message.sequence,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
            )
            for message in page.items
        ),
        next_after_sequence=page.next_after_sequence,
        has_more=page.has_more,
    )


def _turn_response(turn: TurnReadModel) -> V1TurnResponse:
    return V1TurnResponse(
        turn_id=turn.turn_id,
        conversation_id=turn.conversation_id,
        input_message_id=turn.input_message_id,
        agent_version_id=turn.agent_version_id,
        status=turn.status,
        created_at=turn.created_at,
        completed_at=turn.completed_at,
        attempts=tuple(
            V1TurnAttemptResponse(
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                created_at=attempt.created_at,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
            )
            for attempt in turn.attempts
        ),
        events_url=turn_events_url(turn.turn_id),
    )
