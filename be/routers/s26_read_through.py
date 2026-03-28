"""
2.6 — Read-Through Pattern
aiocache @cached decorator — the cache layer fetches from DB on miss.
The endpoint code has zero Redis commands; @cached owns the entire lifecycle.
"""

import asyncio
import time
from typing import Optional

import redis.asyncio as aioredis
from aiocache import Cache, cached
from aiocache.serializers import JsonSerializer
from core.database import AsyncSessionLocal
from core.models import CatalogItem
from core.redis_client import AIOCACHE_HOST, AIOCACHE_PORT, get_redis
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/v1/read-through", tags=["2.6 Read-Through"])

_RT_TTL = 240  # 4-minute TTL for catalog items
_rt_hits: int = 0
_rt_misses: int = 0


def _rt_key_builder(func, article_id: int) -> str:
    return f"article:{article_id}"


@cached(
    ttl=_RT_TTL,
    cache=Cache.REDIS,
    endpoint=AIOCACHE_HOST,
    port=AIOCACHE_PORT,
    namespace="rt",
    serializer=JsonSerializer(),
    key_builder=_rt_key_builder,
)
async def _rt_fetch_article(article_id: int) -> Optional[dict]:
    """DB loader — body executes ONLY on a cache miss. aiocache owns the rest."""
    await asyncio.sleep(0.18)  # simulate 180 ms DB round-trip
    async with AsyncSessionLocal() as session:
        row = await session.get(CatalogItem, article_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "title": row.title,
            "category": row.category,
            "description": row.description,
            "price": row.price,
        }


@router.get("/article/{article_id}")
async def read_through_get_article(
    article_id: int,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if article_id < 1 or article_id > 6:
        raise HTTPException(
            status_code=404, detail=f"No catalog item with id={article_id} (valid: 1–6)"
        )

    global _rt_hits, _rt_misses

    cache_key = f"rt:article:{article_id}"
    pre_exists = bool(await redis.exists(cache_key))

    t0 = time.perf_counter()
    data = await _rt_fetch_article(article_id)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    if data is None:
        raise HTTPException(
            status_code=404, detail=f"Catalog item {article_id} not found"
        )

    if pre_exists:
        _rt_hits += 1
    else:
        _rt_misses += 1

    return {
        "data": data,
        "cache_hit": pre_exists,
        "source": "aiocache_redis" if pre_exists else "sqlite_db",
        "latency_ms": latency_ms,
        "cache_key": cache_key,
        "ttl_seconds": _RT_TTL,
        "pattern": "read-through",
        "note": "Endpoint code has zero Redis commands — @cached handles the entire cache lifecycle transparently",
    }


@router.get("/stats")
async def read_through_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    total = _rt_hits + _rt_misses
    hit_ratio = round(_rt_hits / total * 100, 1) if total > 0 else 0.0

    cached_keys: list[str] = []
    if redis:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="rt:article:*", count=100)
            cached_keys.extend(keys)
            if cursor == 0:
                break

    return {
        "hits": _rt_hits,
        "misses": _rt_misses,
        "total_requests": total,
        "hit_ratio_pct": hit_ratio,
        "cached_item_count": len(cached_keys),
        "cached_keys": sorted(cached_keys),
        "ttl_seconds": _RT_TTL,
        "strategy": "read-through",
        "library": "aiocache>=0.12",
        "decorator": "@cached(ttl=240, cache=Cache.REDIS, namespace='rt')",
    }


@router.delete("/cache")
async def read_through_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    global _rt_hits, _rt_misses

    deleted_keys: list[str] = []
    if redis:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="rt:*", count=100)
            if keys:
                await redis.delete(*keys)
                deleted_keys.extend(keys)
            if cursor == 0:
                break

    _rt_hits = 0
    _rt_misses = 0

    return {
        "message": "Read-Through cache cleared — all rt:* keys deleted and stats reset.",
        "deleted_keys": sorted(deleted_keys),
        "deleted_count": len(deleted_keys),
        "ok": True,
    }
