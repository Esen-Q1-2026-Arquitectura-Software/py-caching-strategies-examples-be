"""
2.3 — Cache-Aside (Lazy Loading) Pattern
Real SQLAlchemy ORM queries + negative caching to prevent DB hammering on 404s.
"""

import asyncio
import json
import threading
import time
from typing import Optional

import redis.asyncio as aioredis
from core.database import get_db
from core.models import Order
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/v1/cache-aside", tags=["2.3 Cache-Aside"])

_CA_TTL = 120  # positive result TTL (2 min)
_CA_NULL_TTL = 15  # negative result TTL (15 s) — prevents DB hammering on 404s

_ca_hits = 0
_ca_misses = 0
_ca_neg_hits = 0
_ca_lock = threading.Lock()


def _ca_hit():
    global _ca_hits
    with _ca_lock:
        _ca_hits += 1


def _ca_miss():
    global _ca_misses
    with _ca_lock:
        _ca_misses += 1


def _ca_neg_hit():
    global _ca_neg_hits
    with _ca_lock:
        _ca_neg_hits += 1


@router.get("/order/{order_id}")
async def cache_aside_get_order(
    order_id: int,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if order_id < 1:
        raise HTTPException(
            status_code=400, detail="order_id must be a positive integer"
        )

    cache_key = f"order:{order_id}"
    start = time.perf_counter()

    cached_raw = await redis.get(cache_key)
    if cached_raw is not None:
        ttl_remaining = await redis.ttl(cache_key)
        latency_ms = (time.perf_counter() - start) * 1000

        if cached_raw == "null":
            _ca_neg_hit()
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Order {order_id} not found",
                    "cache_hit": True,
                    "source": "redis_negative_cache",
                    "latency_ms": round(latency_ms, 2),
                    "ttl_remaining_seconds": ttl_remaining,
                    "note": f"Negative cache HIT — DB NOT queried. Null cached for {_CA_NULL_TTL}s to prevent DB hammering on repeated 404s.",
                },
            )

        _ca_hit()
        return {
            "data": json.loads(cached_raw),
            "cache_hit": True,
            "source": "redis_cache_aside",
            "latency_ms": round(latency_ms, 2),
            "ttl_remaining_seconds": ttl_remaining,
            "cache_key": cache_key,
            "note": "Cache-Aside HIT — Redis served the order. SQLAlchemy DB not touched.",
        }

    await asyncio.sleep(0.180)  # simulate realistic I/O latency
    result = await db.execute(select(Order).where(Order.id == order_id))
    order_row = result.scalar_one_or_none()

    if order_row is None:
        await redis.setex(cache_key, _CA_NULL_TTL, "null")
        _ca_miss()
        latency_ms = (time.perf_counter() - start) * 1000
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Order {order_id} not found",
                "cache_hit": False,
                "source": "sqlalchemy_db",
                "latency_ms": round(latency_ms, 2),
                "note": f"Null result cached for {_CA_NULL_TTL}s (negative caching). Next lookup for this ID will skip the DB entirely.",
            },
        )

    order_dict = {
        "id": order_row.id,
        "customer": order_row.customer,
        "status": order_row.status,
        "amount": order_row.amount,
        "items": order_row.items,
    }
    await redis.setex(cache_key, _CA_TTL, json.dumps(order_dict))
    _ca_miss()

    return {
        "data": order_dict,
        "cache_hit": False,
        "source": "sqlalchemy_db",
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "ttl_remaining_seconds": _CA_TTL,
        "cache_key": cache_key,
        "note": f"Cache-Aside MISS — fetched from SQLAlchemy, now cached in Redis for {_CA_TTL}s.",
    }


@router.get("/stats")
async def cache_aside_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    with _ca_lock:
        hits, misses, neg_hits = _ca_hits, _ca_misses, _ca_neg_hits
    total = hits + misses

    cached_keys = []
    async for key in redis.scan_iter("order:*"):
        ttl = await redis.ttl(key)
        val = await redis.get(key)
        cached_keys.append(
            {
                "key": key,
                "type": "negative_null" if val == "null" else "positive",
                "ttl_remaining_seconds": ttl,
            }
        )

    return {
        "strategy": "Cache-Aside (Lazy Loading)",
        "hits": hits,
        "misses": misses,
        "negative_cache_hits": neg_hits,
        "hit_rate_pct": round(hits / max(total, 1) * 100, 1),
        "cached_order_keys": sorted(cached_keys, key=lambda x: x["key"]),
        "positive_ttl_seconds": _CA_TTL,
        "negative_ttl_seconds": _CA_NULL_TTL,
        "note": "Cache populated lazily — only requested IDs get cached. Negative caching prevents DB hammering on repeated 404s.",
    }


@router.delete("/cache")
async def cache_aside_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    deleted = 0
    async for key in redis.scan_iter("order:*"):
        await redis.delete(key)
        deleted += 1

    global _ca_hits, _ca_misses, _ca_neg_hits
    with _ca_lock:
        _ca_hits = _ca_misses = _ca_neg_hits = 0

    return {
        "message": f"Cleared {deleted} order key(s) — next request will be a cache miss",
        "deleted_count": deleted,
    }
