"""FastAPI application factory."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from switchboard import __version__
from switchboard.adapters.api.conversations import create_conversation_router
from switchboard.adapters.api.dependencies import ConversationApiServices
from switchboard.adapters.api.errors import install_v1_error_handlers
from switchboard.adapters.api.health import create_health_router
from switchboard.adapters.api.turn_events import create_turn_events_router
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.bootstrap.config import Settings

CloseResources = Callable[[], Awaitable[None]]


def create_app(
    settings: Settings,
    readiness_service: ReadinessService,
    close_resources: CloseResources | None = None,
    replay_turn_events: ReplayTurnEvents | None = None,
    conversation_api_services: ConversationApiServices | None = None,
) -> FastAPI:
    """Create and configure the Switchboard API."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if close_resources is not None:
                await close_resources()

    app = FastAPI(
        title="Switchboard API",
        description=(
            "Shared infrastructure for AI conversations, tool routing, "
            "safe execution, evaluation, and rollout."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    app.state.settings = settings
    install_v1_error_handlers(app)
    app.include_router(create_health_router(readiness_service))

    if conversation_api_services is not None:
        app.include_router(create_conversation_router(conversation_api_services))

    if replay_turn_events is not None:
        app.include_router(create_turn_events_router(replay_turn_events))

    return app
