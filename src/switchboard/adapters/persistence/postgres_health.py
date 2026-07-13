"""PostgreSQL readiness probe."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresHealthProbe:
    """Checks whether PostgreSQL accepts a simple query."""

    name = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        """Execute a minimal database query."""

        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
