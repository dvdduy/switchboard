"""FastAPI application factory."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from switchboard import __version__
from switchboard.adapters.api.health import create_health_router
from switchboard.application.services.readiness import ReadinessService
from switchboard.bootstrap.config import Settings

CloseResources = Callable[[], Awaitable[None]]


def create_app(
    settings: Settings,
    readiness_service: ReadinessService,
    close_resources: CloseResources | None = None,
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
    app.include_router(create_health_router(readiness_service))

    return app
