"""
2.2 — Redis SETEX/GET
Cache-aside pattern with a connection pool. Distributed cache shared across
all FastAPI workers/instances.
"""

import asyncio
import json
import time
from typing import Optional

import redis.asyncio as aioredis
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/v1/redis", tags=["2.2 Redis"])

_PRODUCT_DATA: dict[int, dict] = {
    1: {
        "id": 1,
        "name": "Laptop Pro 15",
        "category": "Electronics",
        "price": 1299.99,
        "description": "High-performance laptop with M-series chip",
    },
    2: {
        "id": 2,
        "name": "Wireless Mouse",
        "category": "Electronics",
        "price": 29.99,
        "description": "Ergonomic wireless mouse, 3-year battery life",
    },
    3: {
        "id": 3,
        "name": "Standing Desk",
        "category": "Furniture",
        "price": 449.99,
        "description": "Electric height-adjustable standing desk",
    },
    4: {
        "id": 4,
        "name": "Coffee Maker",
        "category": "Appliances",
        "price": 89.99,
        "description": "Programmable 12-cup drip coffee maker",
    },
    5: {
        "id": 5,
        "name": "Notebook Pack",
        "category": "Stationery",
        "price": 12.99,
        "description": "Pack of 5 college-ruled spiral notebooks",
    },
}

_REDIS_TTL = 300  # 5 minutes


async def _db_fetch_product(product_id: int) -> Optional[dict]:
    await asyncio.sleep(0.200)  # simulate 200 ms DB latency
    return _PRODUCT_DATA.get(product_id)


@router.get("/product/{product_id}")
async def redis_get_product(
    product_id: int,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Redis is unavailable — start Redis and restart the backend",
        )
    if product_id < 1 or product_id > 5:
        raise HTTPException(
            status_code=400, detail="product_id must be 1–5 for this demo"
        )

    cache_key = f"product:{product_id}"
    start = time.perf_counter()

    cached_raw = await redis.get(cache_key)
    if cached_raw:
        ttl_remaining = await redis.ttl(cache_key)
        return {
            "data": json.loads(cached_raw),
            "cache_hit": True,
            "source": "redis",
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "ttl_remaining_seconds": ttl_remaining,
            "cache_key": cache_key,
            "note": "Redis SETEX/GET — distributed cache shared across all workers/instances",
        }

    product = await _db_fetch_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    await redis.setex(cache_key, _REDIS_TTL, json.dumps(product))
    return {
        "data": product,
        "cache_hit": False,
        "source": "simulated_db",
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "ttl_remaining_seconds": _REDIS_TTL,
        "cache_key": cache_key,
        "note": "Redis SETEX/GET — distributed cache shared across all workers/instances",
    }


@router.get("/stats")
async def redis_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    server_info = await redis.info("server")
    stats_info = await redis.info("stats")

    cached_keys = []
    async for key in redis.scan_iter("product:*"):
        ttl = await redis.ttl(key)
        cached_keys.append({"key": key, "ttl_remaining_seconds": ttl})

    return {
        "strategy": "Redis SETEX/GET (cache-aside)",
        "redis_version": server_info.get("redis_version"),
        "keyspace_hits": stats_info.get("keyspace_hits", 0),
        "keyspace_misses": stats_info.get("keyspace_misses", 0),
        "cached_product_keys": sorted(cached_keys, key=lambda x: x["key"]),
        "product_ttl_seconds": _REDIS_TTL,
        "note": "SCAN used instead of KEYS — safe in production (non-blocking)",
    }


@router.delete("/cache")
async def redis_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    deleted = 0
    async for key in redis.scan_iter("product:*"):
        await redis.delete(key)
        deleted += 1

    return {
        "message": f"Cleared {deleted} product key(s) from Redis — next call will be a miss",
        "deleted_count": deleted,
    }
