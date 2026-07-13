from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.application.services.readiness import ReadinessService
from switchboard.bootstrap.config import Settings


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": ("postgresql+psycopg://user:password@localhost:5432/switchboard_test"),
            "redis_url": "redis://localhost:6379/15",
            "log_level": "DEBUG",
        }
    )


def make_empty_readiness_service() -> ReadinessService:
    return ReadinessService(probes=())


async def test_liveness_endpoint_reports_alive() -> None:
    app = create_app(
        settings=make_test_settings(),
        readiness_service=make_empty_readiness_service(),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_application_exposes_versioned_openapi_document() -> None:
    app = create_app(
        settings=make_test_settings(),
        readiness_service=make_empty_readiness_service(),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200

    document = response.json()
    assert document["info"]["title"] == "Switchboard API"
    assert document["info"]["version"] == "0.1.0"
