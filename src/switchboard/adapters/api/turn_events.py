"""Reconnectable SSE delivery for durable turn execution events."""

import json
from collections.abc import AsyncIterator, Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from switchboard.application.errors import TurnNotFoundError
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.domain.execution_events import ExecutionEvent
from switchboard.domain.identifiers import TurnId


def _to_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _to_json_value(nested) for key, nested in value.items()}

    if isinstance(value, tuple):
        return [_to_json_value(item) for item in value]

    return value


def serialize_sse_event(event: ExecutionEvent) -> str:
    """Serialize one durable event as an exact SSE frame."""

    data = json.dumps(
        _to_json_value(event.payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )

    return f"id: {event.sequence}\nevent: {event.kind.value}\ndata: {data}\n\n"


async def _serialize_events(
    events: AsyncIterator[ExecutionEvent],
) -> AsyncIterator[str]:
    async for event in events:
        yield serialize_sse_event(event)


def parse_last_event_id(value: str | None) -> int:
    """Parse an exclusive SSE cursor without permissive numeric coercion."""

    if value is None:
        return 0

    if not value or not value.isascii() or not value.isdecimal():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must be a non-negative integer",
        )

    return int(value)


def create_turn_events_router(
    replay_turn_events: ReplayTurnEvents,
) -> APIRouter:
    """Create routes that expose committed turn events without starting work."""

    router = APIRouter(prefix="/api/v1/turns", tags=["turn-events"])

    @router.get("/{turn_id}/events")
    async def stream_turn_events(
        turn_id: UUID,
        last_event_id: Annotated[
            str | None,
            Header(alias="Last-Event-ID"),
        ] = None,
    ) -> StreamingResponse:
        cursor = parse_last_event_id(last_event_id)

        try:
            observer = await replay_turn_events.open(
                turn_id=TurnId(turn_id),
                after_sequence=cursor,
            )
        except TurnNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error

        return StreamingResponse(
            _serialize_events(observer),
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )

    return router
