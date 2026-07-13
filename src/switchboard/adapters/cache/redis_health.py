"""Redis readiness probe."""

from redis.asyncio import Redis


class RedisHealthProbe:
    """Checks whether Redis responds to commands."""

    name = "redis"

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def check(self) -> None:
        """Ping Redis and require a successful response."""

        response = await self._client.ping()

        if response is not True:
            raise ConnectionError("Redis ping did not return a successful response")
