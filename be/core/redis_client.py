import os
from typing import Optional

import redis.asyncio as aioredis

# Module-level Redis client — set by lifespan on startup, read by middleware and routers.
_redis: Optional[aioredis.Redis] = None

_REDIS_URL_RAW = os.getenv("REDIS_URL", "redis://localhost:6379")

# Parsed for aiocache @cached decorator (applied at import time in s26_read_through).
AIOCACHE_HOST: str = _REDIS_URL_RAW.split("://")[1].split(":")[0]
AIOCACHE_PORT: int = 6379


async def get_redis() -> Optional[aioredis.Redis]:
    return _redis
