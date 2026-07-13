from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.application.services.readiness import ReadinessService
from switchboard.bootstrap.config import Settings


@dataclass
class StubHealthProbe:
    """Controllable dependency probe for readiness tests."""

    name: str
    error: Exception | None = None

    async def check(self) -> None:
        if self.error is not None:
            raise self.error


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": ("postgresql+psycopg://user:password@localhost:5432/switchboard_test"),
            "redis_url": "redis://localhost:6379/15",
        }
    )


async def test_readiness_returns_200_when_all_dependencies_are_available() -> None:
    readiness_service = ReadinessService(
        probes=(
            StubHealthProbe(name="postgres"),
            StubHealthProbe(name="redis"),
        )
    )

    app = create_app(
        settings=make_test_settings(),
        readiness_service=readiness_service,
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {
            "postgres": "available",
            "redis": "available",
        },
    }


async def test_readiness_returns_503_when_a_dependency_is_unavailable() -> None:
    readiness_service = ReadinessService(
        probes=(
            StubHealthProbe(
                name="postgres",
                error=ConnectionError("database unavailable"),
            ),
            StubHealthProbe(name="redis"),
        )
    )

    app = create_app(
        settings=make_test_settings(),
        readiness_service=readiness_service,
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        readiness_response = await client.get("/health/ready")
        liveness_response = await client.get("/health/live")

    assert readiness_response.status_code == 503
    assert readiness_response.json() == {
        "status": "not_ready",
        "dependencies": {
            "postgres": "unavailable",
            "redis": "available",
        },
    }

    assert liveness_response.status_code == 200
    assert liveness_response.json() == {"status": "alive"}
