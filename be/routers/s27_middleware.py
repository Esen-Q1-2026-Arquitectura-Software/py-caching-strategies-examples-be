"""
2.7 — FastAPI Response Caching Middleware (endpoints only)
The ResponseCacheMiddleware in middleware/response_cache.py is the entire caching layer.
Route handlers here contain ZERO Redis logic — the middleware handles everything.
"""

import asyncio
from typing import Optional

import middleware.response_cache as _mc_state
import redis.asyncio as aioredis
from core.database import get_db
from core.models import Track
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, HTTPException
from middleware.response_cache import MC_PREFIX, MC_TTL, mc_hits, mc_misses
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/v1/middleware", tags=["2.7 Response Cache Middleware"])


@router.get("/catalog")
async def middleware_catalog(db: AsyncSession = Depends(get_db)):
    """
    Returns all tracks. On cache MISS the handler runs (~150 ms DB delay).
    On cache HIT the middleware short-circuits — this body never executes.
    """
    await asyncio.sleep(0.150)
    result = await db.execute(select(Track).order_by(Track.id))
    tracks = result.scalars().all()
    return {
        "tracks": [
            {
                "id": t.id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "genre": t.genre,
                "duration_s": t.duration_s,
            }
            for t in tracks
        ],
        "count": len(tracks),
        "ttl_seconds": MC_TTL,
        "note": "Check X-Cache header: HIT = served from Redis by middleware; MISS = handler ran.",
    }


@router.get("/track/{track_id}")
async def middleware_track(track_id: int, db: AsyncSession = Depends(get_db)):
    """Returns a single track. Cached per track_id by the middleware."""
    if track_id < 1 or track_id > 5:
        raise HTTPException(
            status_code=404, detail=f"No track with id={track_id} (valid: 1–5)"
        )
    await asyncio.sleep(0.150)
    track = await db.get(Track, track_id)
    if not track:
        raise HTTPException(status_code=404, detail=f"Track {track_id} not found.")
    return {
        "id": track.id,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "genre": track.genre,
        "duration_s": track.duration_s,
        "ttl_seconds": MC_TTL,
        "note": "Check X-Cache header: HIT = this function body did NOT run.",
    }


@router.get("/stats")
async def middleware_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """
    Live hit/miss counters and cached mc:* keys.
    Excluded from middleware caching so it always reflects real-time state.
    """
    hits = _mc_state.mc_hits
    misses = _mc_state.mc_misses
    total = hits + misses
    hit_ratio = round(hits / total * 100, 1) if total > 0 else 0.0

    cached_keys: list[str] = []
    if redis:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{MC_PREFIX}*", count=100)
            cached_keys.extend(keys)
            if cursor == 0:
                break

    return {
        "hits": hits,
        "misses": misses,
        "total_requests": total,
        "hit_ratio_pct": hit_ratio,
        "cached_item_count": len(cached_keys),
        "cached_keys": sorted(cached_keys),
        "ttl_seconds": MC_TTL,
        "strategy": "response-caching-middleware",
        "key_prefix": MC_PREFIX,
        "middleware_class": "ResponseCacheMiddleware (BaseHTTPMiddleware)",
    }


@router.delete("/cache")
async def middleware_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Evict all middleware-cached HTTP responses and reset counters."""
    deleted_keys: list[str] = []
    if redis:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{MC_PREFIX}*", count=100)
            if keys:
                await redis.delete(*keys)
                deleted_keys.extend(keys)
            if cursor == 0:
                break

    _mc_state.mc_hits = 0
    _mc_state.mc_misses = 0

    return {
        "message": "Middleware cache cleared — all mc:* keys deleted and stats reset.",
        "deleted_keys": sorted(deleted_keys),
        "deleted_count": len(deleted_keys),
        "ok": True,
    }
