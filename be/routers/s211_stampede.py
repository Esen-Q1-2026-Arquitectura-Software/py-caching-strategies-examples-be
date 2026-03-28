"""
2.11 — Cache Stampede Prevention (Mutex Lock)
=============================================
When a popular cache key expires, a burst of concurrent requests all find
a cache miss and all hit the database simultaneously — the "stampede effect"
(a.k.a. dogpile / thundering herd on a single key).

Prevention: distributed Redis NX lock.  Only ONE request rebuilds the cache;
all others spin-wait and retry the cache read.

Core pattern:
    # 1. Fast path
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)                    # HIT — no lock needed

    # 2. Race to acquire lock (NX = set only if not exists)
    acquired = await redis.set(lock_key, "1", nx=True, ex=10)

    if acquired:
        try:
            result = await expensive_db_call()
            await redis.setex(key, ttl, json.dumps(result))
            return result
        finally:
            await redis.delete(lock_key)             # always release
    else:
        # 3. Spin-wait: someone else is rebuilding
        for _ in range(20):                          # up to 2 seconds
            await asyncio.sleep(0.1)
            cached = await redis.get(key)
            if cached:
                return json.loads(cached)            # got it from lock holder
        # 4. Timeout fallback — lock holder died before writing
        return await expensive_db_call()
"""

import asyncio
import json
import random
import time

import redis.asyncio as aioredis
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/v1/stampede", tags=["2.11 Stampede Prevention"])

_NS = "sp211"
_KEY_SAFE = f"{_NS}:trending:safe"
_KEY_UNSAFE = f"{_NS}:trending:unsafe"
_LOCK_KEY = f"{_NS}:lock:trending"
_TTL = 30  # short TTL — lets the demo expire quickly
_COMPUTE_DELAY = 0.15  # 150 ms → simulates an expensive query / computation

# ── Module-level metrics ──────────────────────────────────────────────────────
_metrics: dict = {
    "hits_safe": 0,
    "misses_safe": 0,
    "hits_unsafe": 0,
    "misses_unsafe": 0,
    "db_calls": 0,
    "lock_acquired": 0,
    "lock_waited": 0,
    "lock_timeouts": 0,
    "total_safe": 0,
    "total_unsafe": 0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _trending_payload() -> dict:
    """Generate a random 'trending topics' result — simulates DB output."""
    topics = ["AI", "FastAPI", "Redis", "Python", "Rust", "K8s", "LLMs", "Docker"]
    random.shuffle(topics)
    return {
        "trending": [
            {
                "rank": i + 1,
                "topic": topics[i % len(topics)],
                "score": round(random.uniform(60, 100), 1),
                "delta": random.choice(["+", "−"]) + str(random.randint(1, 20)),
            }
            for i in range(6)
        ],
        "computed_at": round(time.time(), 3),
        "compute_ms": int(_COMPUTE_DELAY * 1000),
    }


async def _expensive_db_call(counters: dict | None = None) -> dict:
    """Simulate a slow DB query (150 ms) and increment call counters."""
    _metrics["db_calls"] += 1
    if counters is not None:
        counters["db_calls"] = counters.get("db_calls", 0) + 1
    await asyncio.sleep(_COMPUTE_DELAY)
    return _trending_payload()


# ── Without-lock path ─────────────────────────────────────────────────────────


async def _fetch_unsafe(redis: aioredis.Redis) -> dict:
    """Cache-aside WITHOUT any stampede protection."""
    _metrics["total_unsafe"] += 1
    t0 = time.perf_counter()

    cached = await redis.get(_KEY_UNSAFE)
    if cached:
        _metrics["hits_unsafe"] += 1
        data = json.loads(cached)
        data["cache"] = "HIT"
        data["protected"] = False
        data["lock_status"] = "none"
        data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return data

    _metrics["misses_unsafe"] += 1
    result = await _expensive_db_call()
    result["cache"] = "MISS"
    result["protected"] = False
    result["lock_status"] = "none"
    await redis.setex(_KEY_UNSAFE, _TTL, json.dumps(result))
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


# ── With-lock path ────────────────────────────────────────────────────────────


async def _fetch_safe(redis: aioredis.Redis) -> dict:
    """Cache-aside WITH distributed NX lock — stampede protected."""
    _metrics["total_safe"] += 1
    t0 = time.perf_counter()

    # 1. Fast path — cache hit (no lock needed)
    cached = await redis.get(_KEY_SAFE)
    if cached:
        _metrics["hits_safe"] += 1
        data = json.loads(cached)
        data["cache"] = "HIT"
        data["protected"] = True
        data["lock_status"] = "not_needed"
        data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return data

    _metrics["misses_safe"] += 1

    # 2. Try to acquire lock (atomic SET NX EX)
    acquired = await redis.set(_LOCK_KEY, "1", nx=True, ex=10)

    if acquired:
        _metrics["lock_acquired"] += 1
        try:
            result = await _expensive_db_call()
            result["cache"] = "MISS"
            result["protected"] = True
            result["lock_status"] = "acquired"
            await redis.setex(_KEY_SAFE, _TTL, json.dumps(result))
            result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return result
        finally:
            await redis.delete(_LOCK_KEY)
    else:
        # 3. Someone else holds the lock — spin-wait up to 2 s
        _metrics["lock_waited"] += 1
        for _ in range(20):
            await asyncio.sleep(0.1)
            cached = await redis.get(_KEY_SAFE)
            if cached:
                data = json.loads(cached)
                data["cache"] = "HIT"
                data["protected"] = True
                data["lock_status"] = "waited"
                data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                return data

        # 4. Timeout fallback — lock holder crashed before writing
        _metrics["lock_timeouts"] += 1
        result = await _expensive_db_call()
        result["cache"] = "MISS"
        result["protected"] = True
        result["lock_status"] = "timeout_fallback"
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result


# ── Simulation helpers ────────────────────────────────────────────────────────


async def _sim_one_unsafe(redis: aioredis.Redis, key: str, counters: dict) -> dict:
    """One request inside the stampede simulation (unprotected path)."""
    cached = await redis.get(key)
    if cached:
        return {"source": "cache"}
    counters["db_calls"] += 1
    await asyncio.sleep(_COMPUTE_DELAY)
    payload = json.dumps({"data": "trending", "ts": time.time()})
    # Only the first writer wins; others overwrite which is fine for demo.
    await redis.setex(key, 20, payload)
    return {"source": "db"}


async def _sim_one_safe(
    redis: aioredis.Redis, key: str, lock_key: str, counters: dict
) -> dict:
    """One request inside the simulation (lock-protected path)."""
    cached = await redis.get(key)
    if cached:
        return {"source": "cache", "lock_status": "not_needed"}

    acquired = await redis.set(lock_key, "1", nx=True, ex=10)
    if acquired:
        try:
            counters["db_calls"] += 1
            await asyncio.sleep(_COMPUTE_DELAY)
            payload = json.dumps({"data": "trending", "ts": time.time()})
            await redis.setex(key, 20, payload)
            return {"source": "db", "lock_status": "acquired"}
        finally:
            await redis.delete(lock_key)
    else:
        for _ in range(20):
            await asyncio.sleep(0.1)
            cached = await redis.get(key)
            if cached:
                return {"source": "cache", "lock_status": "waited"}
        counters["db_calls"] += 1
        await asyncio.sleep(_COMPUTE_DELAY)
        return {"source": "db", "lock_status": "timeout"}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/trending/unsafe", summary="Fetch trending data WITHOUT stampede protection"
)
async def trending_unsafe(redis: aioredis.Redis = Depends(get_redis)):
    """
    Plain cache-aside: on a cold key, every concurrent request will call
    the database — the classic stampede.
    """
    return await _fetch_unsafe(redis)


@router.get(
    "/trending/safe", summary="Fetch trending data WITH distributed-lock protection"
)
async def trending_safe(redis: aioredis.Redis = Depends(get_redis)):
    """
    Lock-protected cache-aside: only ONE request rebuilds the cache;
    all others wait and then read from the freshly populated cache.
    """
    return await _fetch_safe(redis)


@router.post(
    "/simulate", summary="Fire N concurrent requests and observe the stampede effect"
)
async def simulate_stampede(
    count: int = Query(
        default=10, ge=2, le=50, description="Number of concurrent requests to fire"
    ),
    mode: str = Query(
        default="safe",
        pattern="^(safe|unsafe)$",
        description="'safe' uses lock; 'unsafe' has no protection",
    ),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Simulate a cache stampede by firing `count` requests simultaneously
    against a cold (just-cleared) cache key.

    - **mode=unsafe**: all requests see MISS → all call the DB → db_calls ≈ count
    - **mode=safe**: only the lock winner calls the DB → db_calls = 1
    """
    sim_key = f"{_NS}:sim:{mode}"
    sim_lock = f"{_NS}:sim:lock"
    counters: dict = {"db_calls": 0}

    # Guarantee cold start
    await redis.delete(sim_key, sim_lock)

    t0 = time.perf_counter()
    if mode == "unsafe":
        tasks = [_sim_one_unsafe(redis, sim_key, counters) for _ in range(count)]
    else:
        tasks = [
            _sim_one_safe(redis, sim_key, sim_lock, counters) for _ in range(count)
        ]

    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    sources = [
        o.get("source", "error") if isinstance(o, dict) else "error" for o in outcomes
    ]
    lock_statuses = [o.get("lock_status") for o in outcomes if isinstance(o, dict)]

    return {
        "mode": mode,
        "concurrent_requests": count,
        "db_calls_made": counters["db_calls"],
        "cache_hits": sources.count("cache"),
        "db_calls_expected": 1 if mode == "safe" else count,
        "stampede_prevented": counters["db_calls"] == 1 if mode == "safe" else None,
        "elapsed_ms": elapsed,
        "lock_statuses": lock_statuses,
        "sources": sources,
    }


@router.get("/lock-state", summary="Real-time state of the Redis lock and cache keys")
async def lock_state(redis: aioredis.Redis = Depends(get_redis)):
    """Poll this endpoint to watch the lock appear and disappear in real time."""
    lock_val = await redis.get(_LOCK_KEY)
    lock_ttl = await redis.ttl(_LOCK_KEY) if lock_val else -2
    safe_ttl = await redis.ttl(_KEY_SAFE)
    unsafe_ttl = await redis.ttl(_KEY_UNSAFE)
    return {
        "lock_held": lock_val is not None,
        "lock_key": _LOCK_KEY,
        "lock_ttl_s": lock_ttl if lock_ttl >= 0 else None,
        "cache_safe_populated": safe_ttl > 0,
        "cache_safe_ttl_s": safe_ttl if safe_ttl > 0 else None,
        "cache_unsafe_populated": unsafe_ttl > 0,
        "cache_unsafe_ttl_s": unsafe_ttl if unsafe_ttl > 0 else None,
    }


@router.delete("/cache", summary="Evict all stampede-demo cache and lock keys")
async def clear_cache(redis: aioredis.Redis = Depends(get_redis)):
    keys = [
        _KEY_SAFE,
        _KEY_UNSAFE,
        _LOCK_KEY,
        f"{_NS}:sim:safe",
        f"{_NS}:sim:unsafe",
        f"{_NS}:sim:lock",
    ]
    deleted = sum([await redis.delete(k) for k in keys])
    return {"deleted": deleted, "keys_cleared": keys}


@router.get("/stats", summary="Hit/miss/lock metrics + live lock state")
async def stats(redis: aioredis.Redis = Depends(get_redis)):
    lock_data = await lock_state(redis)
    total_safe = _metrics["total_safe"] or 1
    total_unsafe = _metrics["total_unsafe"] or 1
    return {
        "metrics": _metrics,
        "hit_rate_safe_pct": round(_metrics["hits_safe"] / total_safe * 100, 1),
        "hit_rate_unsafe_pct": round(_metrics["hits_unsafe"] / total_unsafe * 100, 1),
        "lock_state": lock_data,
        "config": {
            "ttl_s": _TTL,
            "compute_delay_ms": int(_COMPUTE_DELAY * 1000),
            "cache_key_safe": _KEY_SAFE,
            "cache_key_unsafe": _KEY_UNSAFE,
            "lock_key": _LOCK_KEY,
        },
    }


@router.post("/reset", summary="Reset metrics and clear all demo keys")
async def reset(redis: aioredis.Redis = Depends(get_redis)):
    for k in list(_metrics.keys()):
        _metrics[k] = 0
    keys = [
        _KEY_SAFE,
        _KEY_UNSAFE,
        _LOCK_KEY,
        f"{_NS}:sim:safe",
        f"{_NS}:sim:unsafe",
        f"{_NS}:sim:lock",
    ]
    for k in keys:
        await redis.delete(k)
    return {"reset": True}
