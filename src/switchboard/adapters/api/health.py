"""HTTP health endpoints."""

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from switchboard.application.services.readiness import (
    DependencyAvailability,
    ReadinessService,
)


class LivenessResponse(BaseModel):
    """Response returned when the API process is alive."""

    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    """Response describing infrastructure readiness."""

    status: Literal["ready", "not_ready"]
    dependencies: dict[str, DependencyAvailability]


def create_health_router(
    readiness_service: ReadinessService,
) -> APIRouter:
    """Create health endpoints using an explicit readiness service."""

    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("/live", response_model=LivenessResponse)
    async def get_liveness() -> LivenessResponse:
        """Report whether the API process is running."""

        return LivenessResponse(status="alive")

    @router.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    async def get_readiness() -> JSONResponse:
        """Report whether required infrastructure is available."""

        result = await readiness_service.check()

        response = ReadinessResponse(
            status="ready" if result.is_ready else "not_ready",
            dependencies=result.dependencies,
        )

        status_code = 200 if result.is_ready else 503

        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(mode="json"),
        )

    return router
