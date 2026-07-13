"""SQLAlchemy transaction ownership for application workflows."""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from switchboard.adapters.persistence.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyTurnRepository,
)
from switchboard.application.ports.repositories import (
    AgentRepository,
    ConversationRepository,
    TurnRepository,
)


class SqlAlchemyUnitOfWork:
    """Owns one SQLAlchemy session and transaction scope."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session = session_factory()

        self.agents: AgentRepository = SqlAlchemyAgentRepository(self._session)
        self.conversations: ConversationRepository = SqlAlchemyConversationRepository(self._session)
        self.turns: TurnRepository = SqlAlchemyTurnRepository(self._session)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._session.in_transaction():
                await self._session.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class SqlAlchemyUnitOfWorkFactory:
    """Creates independent SQLAlchemy transaction scopes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)
