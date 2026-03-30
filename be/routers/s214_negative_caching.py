"""Strategy 2.14 — Negative Caching & Bloom Filters
====================================================
Two complementary defences against repeated DB hits for non-existent keys:

  1. Negative cache  — store a NOT_FOUND sentinel in Redis (TTL 30 s) so that
                       follow-up lookups for missing IDs never touch the DB.

  2. Bloom filter    — probabilistic existence check using Redis BF commands.
                       Zero false-negatives: if BF says "no", the key is
                       guaranteed absent. Small false-positive rate (~1%).
                       1 million IDs stored in ~1.2 MB of Redis memory.

Flow (protected endpoint):
  Request → BF.EXISTS → if False  → return NOT_FOUND immediately (bloom block)
                      → if True   → check Redis cache
                                    → sentinel  → return NOT_FOUND (neg cache)
                                    → value     → return value    (pos cache)
                                    → miss      → hit DB → backfill cache
                                                            → if None: sentinel
                                                            → if found: JSON

Redis key conventions (namespace nc214):
  nc214:user:{id}   → positive cache JSON  (TTL = POSITIVE_TTL)
                    → sentinel "__NOT_FOUND__" (TTL = NEGATIVE_TTL)
  nc214:bloom:users → Redis Bloom filter  (BF.* commands)
"""

import asyncio
import json
import math
import time

from core.redis_client import get_redis
from fastapi import APIRouter, Depends, Query

router = APIRouter(
    prefix="/v1/negative",
    tags=["2.14 Negative Caching & Bloom Filters"],
)

# ── Simulated "database" — users 1-30 exist ──────────────────────────────────
_USER_DB: dict[int, dict] = {
    uid: {
        "id": uid,
        "name": f"User {uid:02d}",
        "email": f"user{uid}@example.com",
        "role": "admin" if uid in {1, 2, 3} else "user",
        "active": uid % 7 != 0,  # IDs 7, 14, 21, 28 are inactive
    }
    for uid in range(1, 31)
}
_next_id: int = 31  # auto-increment counter for new users

# ── Config ────────────────────────────────────────────────────────────────────
BLOOM_KEY = "nc214:bloom:users"
BLOOM_CAPACITY = 1_000_000  # expected element count (~1.2 MB at 1% error rate)
BLOOM_ERROR_RATE = 0.01  # 1% false-positive rate
POSITIVE_TTL = 120  # seconds — cached user object
NEGATIVE_TTL = 30  # seconds — NOT_FOUND sentinel
SENTINEL = "__NOT_FOUND__"
DB_DELAY = 0.050  # simulated 50 ms database latency

# ── Per-session stats ─────────────────────────────────────────────────────────
_stats: dict[str, int] = {
    "db_queries": 0,
    "pos_cache_hits": 0,
    "neg_cache_hits": 0,
    "bloom_blocks": 0,
    "bloom_passes": 0,
    "writes": 0,
    "deletes": 0,
}


# ── Internal helpers ──────────────────────────────────────────────────────────


def _cache_key(user_id: int) -> str:
    return f"nc214:user:{user_id}"


async def _db_fetch(user_id: int) -> dict | None:
    """Simulate a slow DB read (50 ms)."""
    await asyncio.sleep(DB_DELAY)
    _stats["db_queries"] += 1
    return _USER_DB.get(user_id)


async def _bloom_exists(redis, user_id: int) -> bool:
    """BF.EXISTS → False means definitely absent; True means maybe present."""
    try:
        result = await redis.execute_command("BF.EXISTS", BLOOM_KEY, str(user_id))
        return bool(result)
    except Exception:
        # Bloom filter not yet initialised — treat as "maybe exists"
        return True


async def _bloom_add(redis, user_id: int) -> None:
    try:
        await redis.execute_command("BF.ADD", BLOOM_KEY, str(user_id))
    except Exception:
        pass


async def _ensure_bloom(redis) -> None:
    """Create/re-initialise the Bloom filter and seed it with all current IDs."""
    try:
        try:
            await redis.execute_command(
                "BF.RESERVE", BLOOM_KEY, BLOOM_ERROR_RATE, BLOOM_CAPACITY
            )
        except Exception:
            pass  # already exists — fine, just add the IDs below
        pipe = redis.pipeline()
        for uid in _USER_DB:
            pipe.execute_command("BF.ADD", BLOOM_KEY, str(uid))
        await pipe.execute()
    except Exception:
        pass


async def _get_user_protected(user_id: int, redis) -> tuple[dict | None, str]:
    """
    Full 3-layer lookup: Bloom → negative/positive cache → DB.
    Returns (user_dict | None, source_label).
    """
    # 1. Bloom filter (probabilistic — 0 false negatives)
    maybe_exists = await _bloom_exists(redis, user_id)
    if not maybe_exists:
        _stats["bloom_blocks"] += 1
        return None, "BLOOM_BLOCK"

    _stats["bloom_passes"] += 1

    # 2. Redis cache check
    cached = await redis.get(_cache_key(user_id))
    if cached == SENTINEL:
        _stats["neg_cache_hits"] += 1
        return None, "NEG_CACHE"
    if cached:
        _stats["pos_cache_hits"] += 1
        return json.loads(cached), "POS_CACHE"

    # 3. DB fetch
    user = await _db_fetch(user_id)
    if user is None:
        await redis.setex(_cache_key(user_id), NEGATIVE_TTL, SENTINEL)
        return None, "DB_MISS"

    await redis.setex(_cache_key(user_id), POSITIVE_TTL, json.dumps(user))
    return user, "DB_HIT"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/user/{user_id}")
async def get_user(user_id: int, redis=Depends(get_redis)):
    """Protected lookup: Bloom filter → negative cache → positive cache → DB."""
    t0 = time.perf_counter()
    user, source = await _get_user_protected(user_id, redis)
    latency_ms = round((time.perf_counter() - t0) * 1000, 3)

    ttl = None
    if source not in ("BLOOM_BLOCK",):
        raw_ttl = await redis.ttl(_cache_key(user_id))
        ttl = raw_ttl if raw_ttl > 0 else None

    return {
        "user_id": user_id,
        "found": user is not None,
        "user": user,
        "source": source,
        "latency_ms": latency_ms,
        "cache_ttl_s": ttl,
        "protected": True,
    }


@router.get("/user/{user_id}/bare")
async def get_user_bare(user_id: int):
    """Bare lookup: no Bloom, no negative cache — always hits DB. For comparison."""
    t0 = time.perf_counter()
    user = await _db_fetch(user_id)
    latency_ms = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "user_id": user_id,
        "found": user is not None,
        "user": user,
        "source": "DB_BARE",
        "latency_ms": latency_ms,
        "protected": False,
    }


@router.post("/user")
async def create_user(
    name: str = Query(...),
    email: str = Query(...),
    redis=Depends(get_redis),
):
    """Create a new user — adds to DB, Bloom filter, and positive cache."""
    global _next_id
    user_id = _next_id
    _next_id += 1
    user = {
        "id": user_id,
        "name": name,
        "email": email,
        "role": "user",
        "active": True,
    }
    _USER_DB[user_id] = user
    _stats["writes"] += 1

    await _bloom_add(redis, user_id)
    # Evict any stale negative entry from previous probes
    await redis.delete(_cache_key(user_id))
    await redis.setex(_cache_key(user_id), POSITIVE_TTL, json.dumps(user))

    return {
        "created": True,
        "user": user,
        "note": f"User #{user_id} added to DB, Bloom filter, and positive cache.",
    }


@router.delete("/user/{user_id}")
async def delete_user(user_id: int, redis=Depends(get_redis)):
    """Delete a user — sets negative-cache sentinel immediately."""
    existed = user_id in _USER_DB
    if existed:
        del _USER_DB[user_id]
    _stats["deletes"] += 1

    # Immediately replace any cached entry with the NOT_FOUND sentinel
    await redis.setex(_cache_key(user_id), NEGATIVE_TTL, SENTINEL)

    return {
        "deleted": existed,
        "user_id": user_id,
        "negative_cache_set": True,
        "note": (
            "Bloom filter NOT updated — BF has no delete operation. "
            "The negative cache sentinel prevents DB hammering for this ID."
        ),
    }


@router.get("/bloom/check/{user_id}")
async def bloom_check(user_id: int, redis=Depends(get_redis)):
    """Check if a user_id is in the Bloom filter, and verify against actual DB."""
    t0 = time.perf_counter()
    bloom_says = await _bloom_exists(redis, user_id)
    latency_ms = round((time.perf_counter() - t0) * 1000, 3)
    in_db = user_id in _USER_DB

    if not bloom_says and not in_db:
        verdict = "true_negative"
        verdict_msg = "Correctly blocked — definitely not in DB."
    elif bloom_says and not in_db:
        verdict = "false_positive"
        verdict_msg = "False positive — BF said 'maybe' but ID is not in DB."
    elif bloom_says and in_db:
        verdict = "true_positive"
        verdict_msg = "Correctly passed — ID exists in DB."
    else:
        verdict = "false_negative"
        verdict_msg = "Impossible! Bloom filters never have false negatives."

    return {
        "user_id": user_id,
        "bloom_says_exists": bloom_says,
        "actually_in_db": in_db,
        "verdict": verdict,
        "verdict_msg": verdict_msg,
        "latency_ms": latency_ms,
    }


@router.get("/bloom/info")
async def bloom_info(redis=Depends(get_redis)):
    """Retrieve Bloom filter metadata from Redis."""
    try:
        raw = await redis.execute_command("BF.INFO", BLOOM_KEY)
        # BF.INFO returns a flat list: [field, value, ...]
        info: dict = {}
        for i in range(0, len(raw) - 1, 2):
            k = raw[i].decode() if isinstance(raw[i], bytes) else raw[i]
            v = raw[i + 1]
            if isinstance(v, bytes):
                v = v.decode()
            info[k] = v

        # Compute approximate memory usage
        items_inserted = int(info.get("Number of items inserted", 0))
        capacity = int(info.get("Capacity", BLOOM_CAPACITY))
        size_bytes = int(info.get("Size", 0))
        pct_full = round(items_inserted / capacity * 100, 2) if capacity else 0

        return {
            "bloom_key": BLOOM_KEY,
            "exists": True,
            "capacity": capacity,
            "items_inserted": items_inserted,
            "size_bytes": size_bytes,
            "size_kb": round(size_bytes / 1024, 2),
            "num_filters": info.get("Number of filters"),
            "expansion_rate": info.get("Expansion rate"),
            "pct_full": pct_full,
            "configured_error_rate": BLOOM_ERROR_RATE,
            "bits_per_element": (
                round(size_bytes * 8 / items_inserted, 2) if items_inserted else None
            ),
        }
    except Exception as exc:
        return {
            "bloom_key": BLOOM_KEY,
            "exists": False,
            "error": str(exc),
            "note": "Call POST /v1/negative/bloom/init to initialise.",
        }


@router.post("/bloom/init")
async def bloom_init(redis=Depends(get_redis)):
    """Initialise (or re-create) the Bloom filter seeded with all existing user IDs."""
    await redis.delete(BLOOM_KEY)
    await _ensure_bloom(redis)
    return {
        "initialised": True,
        "seeded_ids": len(_USER_DB),
        "bloom_key": BLOOM_KEY,
        "capacity": BLOOM_CAPACITY,
        "error_rate": BLOOM_ERROR_RATE,
        "note": (
            f"Bloom filter seeded with {len(_USER_DB)} existing user IDs. "
            f"Expected memory: ~{math.ceil(BLOOM_CAPACITY * 1.44 * math.log2(1 / BLOOM_ERROR_RATE) / 8 / 1024)} KB."
        ),
    }


@router.delete("/cache/user/{user_id}")
async def evict_cache(user_id: int, redis=Depends(get_redis)):
    """Evict a single user entry from Redis cache (both positive and negative)."""
    deleted = await redis.delete(_cache_key(user_id))
    return {
        "user_id": user_id,
        "evicted": bool(deleted),
        "note": "Next lookup will consult the Bloom filter then hit DB.",
    }


@router.delete("/cache/flush")
async def flush_cache(redis=Depends(get_redis)):
    """Flush all nc214 cache entries (positive and negative). Bloom unchanged."""
    keys = []
    async for key in redis.scan_iter("nc214:user:*"):
        keys.append(key)
    if keys:
        await redis.delete(*keys)
    return {
        "flushed": len(keys),
        "note": "All nc214 cache entries cleared. Bloom filter untouched.",
    }


@router.get("/stats")
async def stats_endpoint(redis=Depends(get_redis)):
    """Per-layer counters and current cache occupancy."""
    total_lookups = (
        _stats["pos_cache_hits"]
        + _stats["neg_cache_hits"]
        + _stats["bloom_blocks"]
        + _stats["db_queries"]
    )
    db_saves = (
        _stats["pos_cache_hits"] + _stats["neg_cache_hits"] + _stats["bloom_blocks"]
    )

    pos_keys = neg_keys = 0
    async for key in redis.scan_iter("nc214:user:*"):
        val = await redis.get(key)
        if val == SENTINEL:
            neg_keys += 1
        else:
            pos_keys += 1

    return {
        "counters": _stats.copy(),
        "totals": {
            "total_lookups": total_lookups,
            "db_saves": db_saves,
            "db_save_rate_pct": (
                round(db_saves / total_lookups * 100, 1) if total_lookups else 0.0
            ),
        },
        "current_cache": {
            "positive_entries": pos_keys,
            "negative_entries": neg_keys,
        },
        "db_size": len(_USER_DB),
        "config": {
            "positive_ttl_s": POSITIVE_TTL,
            "negative_ttl_s": NEGATIVE_TTL,
            "db_delay_ms": int(DB_DELAY * 1000),
            "bloom_capacity": BLOOM_CAPACITY,
            "bloom_error_rate": BLOOM_ERROR_RATE,
        },
    }


@router.post("/reset")
async def reset(redis=Depends(get_redis)):
    """Reset stats, flush cache, reset DB to 30 users, re-initialise Bloom filter."""
    global _next_id, _USER_DB

    _USER_DB = {
        uid: {
            "id": uid,
            "name": f"User {uid:02d}",
            "email": f"user{uid}@example.com",
            "role": "admin" if uid in {1, 2, 3} else "user",
            "active": uid % 7 != 0,
        }
        for uid in range(1, 31)
    }
    _next_id = 31

    for key in _stats:
        _stats[key] = 0

    keys = []
    async for key in redis.scan_iter("nc214:user:*"):
        keys.append(key)
    if keys:
        await redis.delete(*keys)

    await redis.delete(BLOOM_KEY)
    await _ensure_bloom(redis)

    return {
        "reset": True,
        "db_size": len(_USER_DB),
        "bloom_reinitialised": True,
        "note": "Stats cleared, cache flushed, DB reset to 30 users, Bloom filter rebuilt.",
    }
