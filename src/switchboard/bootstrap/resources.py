"""Construction and cleanup of infrastructure resources."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from switchboard.adapters.cache.redis_health import RedisHealthProbe
from switchboard.adapters.persistence.postgres_health import (
    PostgresHealthProbe,
)
from switchboard.application.services.readiness import ReadinessService
from switchboard.bootstrap.config import Settings


@dataclass
class RuntimeResources:
    """Infrastructure resources owned by one runtime process."""

    database_engine: AsyncEngine
    redis_client: Redis
    readiness_service: ReadinessService

    async def close(self) -> None:
        """Close all runtime-owned resources."""

        await self.redis_client.aclose()
        await self.database_engine.dispose()


def build_runtime_resources(settings: Settings) -> RuntimeResources:
    """Construct infrastructure clients and application services."""

    database_engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )

    redis_client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )

    readiness_service = ReadinessService(
        probes=(
            PostgresHealthProbe(database_engine),
            RedisHealthProbe(redis_client),
        )
    )

    return RuntimeResources(
        database_engine=database_engine,
        redis_client=redis_client,
        readiness_service=readiness_service,
    )
