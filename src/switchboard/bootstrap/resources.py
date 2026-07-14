"""Construction and cleanup of infrastructure resources."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from switchboard.adapters.api.dependencies import (
    ConversationApiServices,
    build_conversation_api_services,
)
from switchboard.adapters.cache.redis_health import RedisHealthProbe
from switchboard.adapters.persistence.postgres_health import (
    PostgresHealthProbe,
)
from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.adapters.streaming.asyncio_sleeper import AsyncioSleeper
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.bootstrap.config import Settings


@dataclass
class RuntimeResources:
    """Infrastructure resources owned by one runtime process."""

    database_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory
    redis_client: Redis
    readiness_service: ReadinessService
    replay_turn_events: ReplayTurnEvents
    conversation_api_services: ConversationApiServices

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

    session_factory = async_sessionmaker(
        database_engine,
        expire_on_commit=False,
    )

    unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(session_factory)

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

    replay_turn_events = ReplayTurnEvents(
        unit_of_work_factory=unit_of_work_factory,
        sleeper=AsyncioSleeper(),
    )
    conversation_api_services = build_conversation_api_services(unit_of_work_factory)

    return RuntimeResources(
        database_engine=database_engine,
        session_factory=session_factory,
        unit_of_work_factory=unit_of_work_factory,
        redis_client=redis_client,
        readiness_service=readiness_service,
        replay_turn_events=replay_turn_events,
        conversation_api_services=conversation_api_services,
    )
