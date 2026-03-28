"""
2.1 — cachetools TTLCache
30-second TTL, thread-safe, LRU eviction.
"""

import asyncio
import threading
import time

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/v1/ttl", tags=["2.1 TTL Cache"])

_TTL_SECONDS = 30
_ttl_store: TTLCache = TTLCache(maxsize=100, ttl=_TTL_SECONDS)
_ttl_lock = threading.Lock()
_ttl_hits = 0
_ttl_misses = 0

_USER_PERMISSIONS_DB: dict[int, list[str]] = {
    1: ["read", "write", "delete", "admin"],
    2: ["read", "write"],
    3: ["read"],
    4: ["read", "write", "publish"],
    5: ["read", "comment"],
}


def _ttl_get_permissions(user_id: int) -> tuple[list[str], bool]:
    global _ttl_hits, _ttl_misses
    key = f"perms:{user_id}"
    with _ttl_lock:
        if key in _ttl_store:
            _ttl_hits += 1
            return _ttl_store[key], True
        time.sleep(0.150)  # simulate 150 ms DB latency
        perms = _USER_PERMISSIONS_DB.get(user_id, ["read"])
        _ttl_store[key] = perms
        _ttl_misses += 1
        return perms, False


@router.get("/user/{user_id}")
async def ttl_get_user(user_id: int):
    if user_id < 1 or user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")
    start = time.perf_counter()
    permissions, cache_hit = await asyncio.to_thread(_ttl_get_permissions, user_id)
    latency_ms = (time.perf_counter() - start) * 1000
    with _ttl_lock:
        cache_size = len(_ttl_store)
    return {
        "data": {"user_id": user_id, "permissions": permissions},
        "cache_hit": cache_hit,
        "source": "ttl_cache" if cache_hit else "simulated_db",
        "latency_ms": round(latency_ms, 2),
        "ttl_seconds": _TTL_SECONDS,
        "cache_size": cache_size,
        "note": f"cachetools.TTLCache — entries auto-expire after {_TTL_SECONDS}s, thread-safe",
    }


@router.get("/stats")
async def ttl_stats():
    global _ttl_hits, _ttl_misses
    with _ttl_lock:
        cache_size = len(_ttl_store)
        max_size = _ttl_store.maxsize
    total = _ttl_hits + _ttl_misses
    return {
        "strategy": "cachetools.TTLCache",
        "hits": _ttl_hits,
        "misses": _ttl_misses,
        "cache_size": cache_size,
        "max_size": max_size,
        "hit_rate_pct": round(_ttl_hits / max(total, 1) * 100, 1),
        "ttl_seconds": _TTL_SECONDS,
        "note": "Thread-safe TTL cache — entries auto-expire, no manual invalidation needed",
    }


@router.delete("/cache")
async def ttl_clear():
    global _ttl_hits, _ttl_misses
    with _ttl_lock:
        _ttl_store.clear()
        _ttl_hits = 0
        _ttl_misses = 0
    return {
        "message": "TTLCache cleared — next call will be a cache miss",
        "cache_size": 0,
    }
