"""
2.13 -- Two-Level Cache (L1 + L2)
===================================
L1 = process-local TTLCache (cachetools) -- nanosecond access, no network.
L2 = Redis -- shared across all instances, ~1 ms network round-trip.
L3 = "database" (simulated 100 ms fetch).

Lookup order:
    L1 hit  --> return immediately  (fastest path)
    L2 hit  --> backfill L1, return (~1 ms)
    L3 hit  --> backfill L2 + L1, return (~100 ms)

Key insight from the strategy guide:
    L1 has a SHORT TTL (5 s) to bound staleness per instance.
    L2 has a LONGER TTL (60 s) as the shared source of truth.
    In a multi-instance deployment every pod has its own L1; stale window = L1 TTL.

Namespace: tl213
Endpoints exposed:
    GET  /v1/two-level/config/{key}          -- main demo: L1->L2->DB lookup
    GET  /v1/two-level/config/{key}/l2-only  -- bypass L1 (shows Redis latency)
    GET  /v1/two-level/config/{key}/db-only  -- bypass both caches (shows DB latency)
    POST /v1/two-level/config/{key}          -- write-through both layers
    GET  /v1/two-level/l1/inspect            -- view L1 contents & TTLs
    DELETE /v1/two-level/l1/evict/{key}      -- evict a single key from L1 only
    DELETE /v1/two-level/l1/flush            -- flush all L1 entries
    DELETE /v1/two-level/l2/flush            -- flush all L2 (Redis) entries
    DELETE /v1/two-level/flush               -- flush both L1 and L2
    GET  /v1/two-level/stats                 -- hit/miss counters per layer
    POST /v1/two-level/reset                 -- reset stats + flush all caches
"""

import asyncio
import json
import time

import redis.asyncio as aioredis
from cachetools import TTLCache
from core.redis_client import get_redis
from fastapi import APIRouter, Body, Depends

router = APIRouter(prefix="/v1/two-level", tags=["2.13 Two-Level Cache"])

_NS = "tl213"
_L1_TTL = 5  # seconds -- short to bound per-instance staleness
_L1_MAX = 200  # max items in L1
_L2_TTL = 60  # seconds -- shared Redis TTL
_DB_DELAY = 0.10  # simulated DB latency (100 ms)

# ── L1: process-local TTLCache ─────────────────────────────────────────────────
# In production every app instance has its own independent L1 dict.
# Staleness window = _L1_TTL seconds.
_l1: TTLCache = TTLCache(maxsize=_L1_MAX, ttl=_L1_TTL)

# ── Stats counters ─────────────────────────────────────────────────────────────
_stats: dict = {
    "l1_hits": 0,
    "l1_misses": 0,
    "l2_hits": 0,
    "l2_misses": 0,
    "db_calls": 0,
    "writes": 0,
    "l1_evictions": 0,
}

# ── Simulated "database" ──────────────────────────────────────────────────────
# A fixed set of config keys so the demo is self-contained.
_DB: dict[str, dict] = {
    "site.name": {"value": "Caching Showcase", "type": "string"},
    "site.theme": {"value": "dark", "type": "string"},
    "feature.redis": {"value": True, "type": "bool"},
    "feature.l2cache": {"value": True, "type": "bool"},
    "limits.rps": {"value": 1000, "type": "int"},
    "limits.burst": {"value": 200, "type": "int"},
    "ttl.l1": {"value": _L1_TTL, "type": "int"},
    "ttl.l2": {"value": _L2_TTL, "type": "int"},
}


async def _db_fetch(key: str) -> dict | None:
    """Simulate a 100 ms DB read."""
    _stats["db_calls"] += 1
    await asyncio.sleep(_DB_DELAY)
    row = _DB.get(key)
    if row is None:
        return None
    return {"key": key, **row, "fetched_at": round(time.time(), 3)}


def _l2_key(key: str) -> str:
    return f"{_NS}:{key}"


# ── Core lookup helper ─────────────────────────────────────────────────────────


async def _get_with_l1_l2(key: str, redis: aioredis.Redis) -> dict | None:
    """
    Full two-level lookup.
    Returns dict enriched with: source, l1_ttl_remaining, l2_ttl_remaining.
    """
    t0 = time.perf_counter()

    # 1. L1 check -- pure dict lookup, no I/O
    if key in _l1:
        _stats["l1_hits"] += 1
        data = dict(_l1[key])
        data["source"] = "L1"
        # cachetools TTLCache doesn't expose per-key remaining TTL, estimate it
        data["l1_ttl_remaining"] = _L1_TTL  # approximate; key is still alive
        data["l2_ttl_remaining"] = None
        data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return data

    _stats["l1_misses"] += 1

    # 2. L2 check -- Redis
    raw = await redis.get(_l2_key(key))
    if raw:
        _stats["l2_hits"] += 1
        data = json.loads(raw)
        _l1[key] = data  # backfill L1
        data = dict(data)
        data["source"] = "L2"
        data["l1_ttl_remaining"] = _L1_TTL  # just written
        data["l2_ttl_remaining"] = await redis.ttl(_l2_key(key))
        data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return data

    _stats["l2_misses"] += 1

    # 3. DB fetch -- populate both layers
    result = await _db_fetch(key)
    if result is None:
        return None

    payload = json.dumps(result)
    await redis.setex(_l2_key(key), _L2_TTL, payload)
    _l1[key] = result

    result = dict(result)
    result["source"] = "DB"
    result["l1_ttl_remaining"] = _L1_TTL
    result["l2_ttl_remaining"] = _L2_TTL
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return result


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/config/{key:path}")
async def get_config(key: str, redis: aioredis.Redis = Depends(get_redis)):
    """
    L1 -> L2 -> DB lookup with automatic backfill.
    First call: DB (100 ms). Second call: L2 Redis (~1 ms). Third call same
    instance within 5 s: L1 (~0 ms).
    """
    result = await _get_with_l1_l2(key, redis)
    if result is None:
        return {"error": f"Key '{key}' not found", "available_keys": list(_DB.keys())}
    return result


@router.get("/config/{key:path}/l2-only")
async def get_config_l2_only(key: str, redis: aioredis.Redis = Depends(get_redis)):
    """Bypass L1 entirely -- always goes to Redis (L2) or DB. Shows Redis latency."""
    t0 = time.perf_counter()
    raw = await redis.get(_l2_key(key))
    if raw:
        data = json.loads(raw)
        data["source"] = "L2_only"
        data["l2_ttl_remaining"] = await redis.ttl(_l2_key(key))
        data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return data
    result = await _db_fetch(key)
    if result is None:
        return {"error": f"Key '{key}' not found"}
    await redis.setex(_l2_key(key), _L2_TTL, json.dumps(result))
    result["source"] = "DB_via_l2only"
    result["l2_ttl_remaining"] = _L2_TTL
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return result


@router.get("/config/{key:path}/db-only")
async def get_config_db_only(key: str):
    """Bypass both caches -- always hits the DB. Shows full DB latency baseline."""
    t0 = time.perf_counter()
    result = await _db_fetch(key)
    if result is None:
        return {"error": f"Key '{key}' not found"}
    result["source"] = "DB_only"
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return result


@router.post("/config/{key:path}")
async def put_config(
    key: str,
    body: dict = Body(...),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Write-through: update the in-memory _DB, write to L2 (Redis), evict L1.
    Demonstrates that a write invalidates L1 to prevent serving stale data.
    """
    _stats["writes"] += 1

    # Update simulated DB
    _DB[key] = {k: v for k, v in body.items() if k not in ("key", "fetched_at")}

    # Write to L2
    payload = {"key": key, **_DB[key], "fetched_at": round(time.time(), 3)}
    await redis.setex(_l2_key(key), _L2_TTL, json.dumps(payload))

    # Evict L1 (stale) -- next read will pick up from L2
    evicted = key in _l1
    if evicted:
        _stats["l1_evictions"] += 1
        del _l1[key]

    return {
        "written": key,
        "value": _DB[key],
        "l1_evicted": evicted,
        "l2_ttl": _L2_TTL,
        "note": "L1 evicted on write. Next L1 read will backfill from L2.",
    }


@router.get("/l1/inspect")
async def inspect_l1(redis: aioredis.Redis = Depends(get_redis)):
    """
    Dump all keys currently in L1 with their values and approximate remaining TTL.
    Also shows what the corresponding L2 TTL is in Redis.
    """
    items = []
    for k, v in list(_l1.items()):
        l2_ttl = await redis.ttl(_l2_key(k))
        items.append(
            {
                "key": k,
                "value": v,
                "l1_ttl_approx": _L1_TTL,  # TTLCache doesn't expose per-key remaining
                "l2_ttl": l2_ttl,
            }
        )
    return {
        "l1_size": len(_l1),
        "l1_maxsize": _L1_MAX,
        "l1_ttl_s": _L1_TTL,
        "l2_ttl_s": _L2_TTL,
        "items": items,
    }


@router.delete("/l1/evict/{key:path}")
async def evict_l1(key: str):
    """Evict one key from L1 only. L2 (Redis) entry is preserved."""
    was_present = key in _l1
    if was_present:
        _stats["l1_evictions"] += 1
        del _l1[key]
    return {
        "key": key,
        "evicted": was_present,
        "note": "L2 entry intact -- next read will backfill L1 from L2.",
    }


@router.delete("/l1/flush")
async def flush_l1():
    """Flush all L1 (in-memory) entries. L2 Redis entries are preserved."""
    count = len(_l1)
    _stats["l1_evictions"] += count
    _l1.clear()
    return {
        "flushed_l1_entries": count,
        "note": "L2 intact. Next reads will warm L1 from L2.",
    }


@router.delete("/l2/flush")
async def flush_l2(redis: aioredis.Redis = Depends(get_redis)):
    """Delete all L2 (Redis) keys for this strategy. L1 entries are preserved."""
    cursor, deleted = 0, 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        if keys:
            deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return {
        "flushed_l2_keys": deleted,
        "note": "L1 still warm. L2 will be repopulated from DB on next miss.",
    }


@router.delete("/flush")
async def flush_all(redis: aioredis.Redis = Depends(get_redis)):
    """Flush both L1 and L2. Next request will go all the way to DB."""
    l1_count = len(_l1)
    _stats["l1_evictions"] += l1_count
    _l1.clear()
    cursor, l2_deleted = 0, 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        if keys:
            l2_deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return {
        "flushed_l1": l1_count,
        "flushed_l2": l2_deleted,
        "note": "Both caches cold. Next request will be a full DB call (~100 ms).",
    }


@router.get("/stats")
async def get_stats(redis: aioredis.Redis = Depends(get_redis)):
    """Return per-layer hit/miss counters and current L1/L2 occupancy."""
    l2_cursor, l2_keys = 0, []
    while True:
        l2_cursor, batch = await redis.scan(l2_cursor, match=f"{_NS}:*", count=100)
        l2_keys.extend(batch)
        if l2_cursor == 0:
            break

    l1_total = _stats["l1_hits"] + _stats["l1_misses"]
    l2_total = _stats["l2_hits"] + _stats["l2_misses"]

    return {
        "l1": {
            "hits": _stats["l1_hits"],
            "misses": _stats["l1_misses"],
            "hit_rate_pct": (
                round(_stats["l1_hits"] / l1_total * 100, 1) if l1_total else 0
            ),
            "current_size": len(_l1),
            "max_size": _L1_MAX,
            "ttl_s": _L1_TTL,
        },
        "l2": {
            "hits": _stats["l2_hits"],
            "misses": _stats["l2_misses"],
            "hit_rate_pct": (
                round(_stats["l2_hits"] / l2_total * 100, 1) if l2_total else 0
            ),
            "current_keys": len(l2_keys),
            "ttl_s": _L2_TTL,
        },
        "db": {
            "calls": _stats["db_calls"],
        },
        "writes": _stats["writes"],
        "l1_evictions": _stats["l1_evictions"],
        "available_keys": list(_DB.keys()),
    }


@router.post("/reset")
async def reset_all(redis: aioredis.Redis = Depends(get_redis)):
    """Reset all stats and flush both cache layers."""
    l1_count = len(_l1)
    _l1.clear()
    cursor, l2_deleted = 0, 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        if keys:
            l2_deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    for k in _stats:
        _stats[k] = 0
    return {
        "message": "Reset complete.",
        "flushed_l1": l1_count,
        "flushed_l2": l2_deleted,
    }
