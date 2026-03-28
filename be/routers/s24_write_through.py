"""
2.4 — Write-Through Pattern
Every PUT writes to SQLAlchemy DB and Redis simultaneously, eliminating stale reads.
"""

import asyncio
import json
import threading
import time
from typing import Optional

import redis.asyncio as aioredis
from core.database import get_db
from core.models import UserProfile
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/v1/write-through", tags=["2.4 Write-Through"])

_WT_TTL = 600  # 10-minute cache TTL

_wt_reads_hit = 0
_wt_reads_miss = 0
_wt_writes = 0
_wt_recent_ops: list[dict] = []
_wt_lock = threading.Lock()
_MAX_WT_OPS = 10


def _wt_record_op(op: dict) -> None:
    global _wt_recent_ops
    with _wt_lock:
        _wt_recent_ops = ([op] + _wt_recent_ops)[:_MAX_WT_OPS]


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    preferences: Optional[dict] = None


@router.get("/profile/{user_id}")
async def write_through_get_profile(
    user_id: int,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if user_id < 1 or user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")

    cache_key = f"profile:{user_id}"
    start = time.perf_counter()

    cached_raw = await redis.get(cache_key)
    if cached_raw is not None:
        ttl_remaining = await redis.ttl(cache_key)
        latency_ms = (time.perf_counter() - start) * 1000
        with _wt_lock:
            global _wt_reads_hit
            _wt_reads_hit += 1
        return {
            "data": json.loads(cached_raw),
            "cache_hit": True,
            "source": "redis_write_through",
            "latency_ms": round(latency_ms, 2),
            "ttl_remaining_seconds": ttl_remaining,
            "cache_key": cache_key,
            "note": "Write-Through HIT — cache is always current because every PUT writes to DB + Redis synchronously.",
        }

    await asyncio.sleep(0.150)  # simulate DB latency
    result = await db.execute(select(UserProfile).where(UserProfile.id == user_id))
    profile_row = result.scalar_one_or_none()
    if profile_row is None:
        raise HTTPException(status_code=404, detail=f"Profile {user_id} not found")

    profile_dict = {
        "id": profile_row.id,
        "name": profile_row.name,
        "email": profile_row.email,
        "bio": profile_row.bio,
        "preferences": profile_row.preferences,
    }
    await redis.setex(cache_key, _WT_TTL, json.dumps(profile_dict))

    with _wt_lock:
        global _wt_reads_miss
        _wt_reads_miss += 1

    return {
        "data": profile_dict,
        "cache_hit": False,
        "source": "sqlalchemy_db",
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "ttl_remaining_seconds": _WT_TTL,
        "cache_key": cache_key,
        "note": "Cold-start miss — profile loaded from DB and cached. All future GETs AND PUTs will keep the cache current (write-through).",
    }


@router.put("/profile/{user_id}")
async def write_through_update_profile(
    user_id: int,
    body: ProfileUpdate,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if user_id < 1 or user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")

    result = await db.execute(select(UserProfile).where(UserProfile.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Profile {user_id} not found")

    updates: dict = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update provided")

    start = time.perf_counter()

    await db.execute(
        sa_update(UserProfile).where(UserProfile.id == user_id).values(**updates)
    )
    await db.commit()

    result = await db.execute(select(UserProfile).where(UserProfile.id == user_id))
    updated_row = result.scalar_one()
    updated_dict = {
        "id": updated_row.id,
        "name": updated_row.name,
        "email": updated_row.email,
        "bio": updated_row.bio,
        "preferences": updated_row.preferences,
    }

    cache_key = f"profile:{user_id}"
    await redis.setex(cache_key, _WT_TTL, json.dumps(updated_dict))
    db_write_ms = (time.perf_counter() - start) * 1000

    _wt_record_op(
        {
            "op": "PUT",
            "user_id": user_id,
            "fields_updated": list(updates.keys()),
            "db_write_ms": round(db_write_ms, 2),
            "cache_key": cache_key,
        }
    )
    with _wt_lock:
        global _wt_writes
        _wt_writes += 1

    return {
        "data": updated_dict,
        "write_through": True,
        "db_updated": True,
        "cache_updated": True,
        "cache_key": cache_key,
        "cache_ttl_seconds": _WT_TTL,
        "db_write_ms": round(db_write_ms, 2),
        "note": "Write-Through complete — DB and Redis updated atomically. The next GET will be a cache HIT with this exact data.",
    }


@router.get("/stats")
async def write_through_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    with _wt_lock:
        hits = _wt_reads_hit
        misses = _wt_reads_miss
        writes = _wt_writes
        recent_ops = list(_wt_recent_ops)

    total_reads = hits + misses
    cached_keys = []
    async for key in redis.scan_iter("profile:*"):
        ttl = await redis.ttl(key)
        cached_keys.append({"key": key, "ttl_remaining_seconds": ttl})

    return {
        "strategy": "Write-Through",
        "reads_from_cache": hits,
        "reads_from_db_fallback": misses,
        "writes_to_db_and_cache": writes,
        "stale_reads_prevented": writes,
        "read_hit_rate_pct": round(hits / max(total_reads, 1) * 100, 1),
        "cached_profile_keys": sorted(cached_keys, key=lambda x: x["key"]),
        "cache_ttl_seconds": _WT_TTL,
        "recent_ops": recent_ops,
        "note": f"Every PUT writes DB + cache together. This demo has prevented {writes} potential stale-read scenarios.",
    }


@router.delete("/cache")
async def write_through_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    deleted = 0
    async for key in redis.scan_iter("profile:*"):
        await redis.delete(key)
        deleted += 1

    global _wt_reads_hit, _wt_reads_miss, _wt_writes, _wt_recent_ops
    with _wt_lock:
        _wt_reads_hit = _wt_reads_miss = _wt_writes = 0
        _wt_recent_ops = []

    return {
        "message": f"Cleared {deleted} profile key(s). Next GET will be a cold-start miss, then stays hot via write-through.",
        "deleted_count": deleted,
    }
