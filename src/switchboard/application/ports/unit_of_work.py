"""Transaction boundary required by application use cases."""

from types import TracebackType
from typing import Protocol, Self

from switchboard.application.ports.repositories import (
    AgentRepository,
    CommandReceiptRepository,
    ConversationRepository,
    ConversationSummaryRepository,
    ToolInvocationRepository,
    ToolRegistryRepository,
    TurnRepository,
)


class UnitOfWork(Protocol):
    """Owns one atomic application transaction."""

    agents: AgentRepository
    command_receipts: CommandReceiptRepository
    conversations: ConversationRepository
    summaries: ConversationSummaryRepository
    turns: TurnRepository
    tools: ToolRegistryRepository
    tool_invocations: ToolInvocationRepository

    async def __aenter__(self) -> Self:
        """Enter the transactional scope."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back unfinished work and close the transaction."""

    async def commit(self) -> None:
        """Commit all operations performed in this unit of work."""

    async def rollback(self) -> None:
        """Roll back all operations performed in this unit of work."""


class UnitOfWorkFactory(Protocol):
    """Creates an independent transaction scope."""

    def __call__(self) -> UnitOfWork:
        """Create a new unit of work."""
