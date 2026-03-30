"""Strategy 2.15 — Cache Warming Strategies
============================================
Three warming techniques that eliminate cold-start latency and ensure
critical keys are always present in the cache:

  1. Startup Warming    — pre-populate all critical cache keys during
                          application startup (lifespan hook) so that the
                          very first request is always a cache HIT.

  2. Scheduled Refresh  — asyncio background task wakes up before keys expire
                          and proactively refreshes them.  Users never trigger
                          a cache MISS because the scheduler beats the TTL.

  3. Pipeline Bulk Warm — batch all Redis SET commands into a single
                          PIPELINE call (one TCP round-trip).  Warms 20
                          products in ~1 ms instead of ~20 ms one-by-one.

Redis key conventions (namespace cw215):
  cw215:product:{id}   → single product JSON       (TTL = CATALOG_TTL  300 s)
  cw215:config         → site-config JSON           (TTL = CONFIG_TTL   600 s)
  cw215:trending       → top-trending products JSON (TTL = TRENDING_TTL  60 s)
"""

import asyncio
import json
import os
import time
from typing import Any

import redis.asyncio as aioredis
from core.redis_client import get_redis
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/v1/warming",
    tags=["2.15 Cache Warming Strategies"],
)

# ── Config ────────────────────────────────────────────────────────────────────
NAMESPACE = "cw215"
CATALOG_TTL = 300  # 5 min  — product entries
CONFIG_TTL = 600  # 10 min — site config
TRENDING_TTL = 60  # 1 min  — refreshed proactively by scheduler
SCHEDULER_INTERVAL = 45  # seconds between scheduled refresh cycles
DB_DELAY = 0.050  # 50 ms simulated slow DB query

# ── Simulated "database" ──────────────────────────────────────────────────────
_CATEGORIES = ["Electronics", "Books", "Clothing", "Food", "Home"]

_PRODUCTS: dict[int, dict] = {
    pid: {
        "id": pid,
        "name": f"Product {pid:02d}",
        "category": _CATEGORIES[pid % 5],
        "price": round(9.99 + pid * 5.55, 2),
        "stock": pid * 7 % 50 + 10,
        "rating": round(3.0 + (pid % 30) / 15, 1),
        "description": (
            f"High-quality {_CATEGORIES[pid % 5]} item. "
            "Highly rated by thousands of customers."
        ),
    }
    for pid in range(1, 21)
}

_SITE_CONFIG: dict[str, Any] = {
    "site_name": "CacheDemo Store",
    "theme": "dark",
    "max_results_per_page": 20,
    "feature_flags": {"new_checkout": True, "beta_search": False},
    "maintenance_mode": False,
    "support_email": "support@cachedemo.example",
    "version": "3.1.0",
}

_TRENDING_IDS = [3, 7, 12, 1, 15, 18, 5]  # simulated trending product IDs


# ── Per-session stats ─────────────────────────────────────────────────────────
_stats: dict[str, int] = {
    "cache_hits": 0,
    "cache_misses": 0,
    "db_hits": 0,
    "startup_warm_keys": 0,
    "pipeline_warm_keys": 0,
    "scheduled_refresh_runs": 0,
    "pipeline_commands_sent": 0,
}

_warm_log: list[dict] = []  # rolling event log (last 20 entries)
_scheduler_task: asyncio.Task | None = None
_scheduler_running: bool = False
_last_scheduled_refresh: float | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _product_key(pid: int) -> str:
    return f"{NAMESPACE}:product:{pid}"


async def _db_fetch_product(pid: int) -> dict | None:
    """Simulate a 50 ms database read."""
    await asyncio.sleep(DB_DELAY)
    return _PRODUCTS.get(pid)


async def _db_fetch_trending() -> list[dict]:
    await asyncio.sleep(DB_DELAY)
    return [_PRODUCTS[tid] for tid in _TRENDING_IDS if tid in _PRODUCTS]


async def _db_fetch_config() -> dict:
    await asyncio.sleep(DB_DELAY)
    return dict(_SITE_CONFIG)


def _log(event_type: str, detail: str, count: int = 0) -> None:
    global _warm_log
    _warm_log.append(
        {
            "ts": round(time.time(), 2),
            "time_str": time.strftime("%H:%M:%S"),
            "type": event_type,
            "detail": detail,
            "count": count,
        }
    )
    _warm_log = _warm_log[-20:]  # keep only last 20


# ── Strategy 1: Startup Warming ───────────────────────────────────────────────


async def _do_startup_warm(redis: aioredis.Redis) -> dict:
    """Pre-populate all critical cache keys — called on app startup."""
    t0 = time.perf_counter()
    keys_written = 0

    # Warm all 20 products (individual SETs — equivalent to lifespan warm)
    for pid, product in _PRODUCTS.items():
        await redis.setex(_product_key(pid), CATALOG_TTL, json.dumps(product))
        keys_written += 1

    # Warm site config
    config = await _db_fetch_config()
    await redis.setex(f"{NAMESPACE}:config", CONFIG_TTL, json.dumps(config))
    keys_written += 1

    # Warm trending
    trending = await _db_fetch_trending()
    await redis.setex(f"{NAMESPACE}:trending", TRENDING_TTL, json.dumps(trending))
    keys_written += 1

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    _stats["startup_warm_keys"] += keys_written
    _stats["db_hits"] += len(_PRODUCTS) + 2  # simulated — all data fetched from DB
    _log(
        "startup", f"Startup warm: {keys_written} keys in {elapsed_ms} ms", keys_written
    )

    return {
        "method": "startup_warming",
        "keys_written": keys_written,
        "elapsed_ms": elapsed_ms,
        "ttls": {
            "products": f"{CATALOG_TTL} s",
            "config": f"{CONFIG_TTL} s",
            "trending": f"{TRENDING_TTL} s",
        },
    }


# ── Strategy 3: Pipeline Bulk Warming ────────────────────────────────────────


async def _do_pipeline_warm(redis: aioredis.Redis) -> dict:
    """Warm all catalog items in a single PIPELINE — one TCP round-trip."""
    t0 = time.perf_counter()
    commands = 0

    async with redis.pipeline(transaction=False) as pipe:
        for pid, product in _PRODUCTS.items():
            pipe.setex(_product_key(pid), CATALOG_TTL, json.dumps(product))
            commands += 1
        await pipe.execute()

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    _stats["pipeline_warm_keys"] += commands
    _stats["pipeline_commands_sent"] += commands
    _log(
        "pipeline",
        f"Pipeline: {commands} SET commands batched — {elapsed_ms} ms total",
        commands,
    )

    return {
        "method": "pipeline_warming",
        "commands_batched": commands,
        "elapsed_ms": elapsed_ms,
        "note": (
            f"{commands} SET commands in one TCP round-trip vs "
            f"~{commands * 1} ms individually."
        ),
    }


# ── Strategy 2: Scheduled Proactive Refresh ───────────────────────────────────


async def _scheduler_loop() -> None:
    """
    Background asyncio task — wakes every SCHEDULER_INTERVAL seconds and
    refreshes trending + config BEFORE their TTLs expire.
    Users never see a cold cache miss for these keys.
    """
    global _scheduler_running, _last_scheduled_refresh

    # Obtain the shared Redis connection
    import core.redis_client as _redis_mod

    while _scheduler_running:
        await asyncio.sleep(SCHEDULER_INTERVAL)
        if not _scheduler_running:
            break

        r = _redis_mod._redis
        if r is None:
            continue

        t0 = time.perf_counter()
        try:
            # Proactively refresh trending
            trending = await _db_fetch_trending()
            await r.setex(f"{NAMESPACE}:trending", TRENDING_TTL, json.dumps(trending))

            # Proactively refresh config
            config = await _db_fetch_config()
            await r.setex(f"{NAMESPACE}:config", CONFIG_TTL, json.dumps(config))

            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            _last_scheduled_refresh = time.time()
            _stats["scheduled_refresh_runs"] += 1
            _stats["db_hits"] += 2
            _log(
                "scheduled",
                f"Scheduled refresh #{_stats['scheduled_refresh_runs']}: "
                f"trending + config refreshed in {elapsed_ms} ms",
                2,
            )
        except Exception as exc:
            _log("error", f"Scheduler refresh failed: {exc}", 0)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/product/{product_id}")
async def get_product(
    product_id: int,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Cache-aside lookup for a single product with latency measurement."""
    key = _product_key(product_id)
    t0 = time.perf_counter()

    cached = await redis.get(key)
    if cached:
        _stats["cache_hits"] += 1
        return {
            "source": "cache",
            "cache_result": "HIT",
            "product": json.loads(cached),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    _stats["cache_misses"] += 1
    product = await _db_fetch_product(product_id)
    _stats["db_hits"] += 1

    if product is None:
        return {
            "source": "db",
            "cache_result": "MISS",
            "product": None,
            "error": f"Product {product_id} not found (valid: 1-20)",
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    await redis.setex(key, CATALOG_TTL, json.dumps(product))
    return {
        "source": "db",
        "cache_result": "MISS",
        "product": product,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@router.get("/trending")
async def get_trending(redis: aioredis.Redis = Depends(get_redis)):
    """
    Trending products — kept perpetually warm by the background scheduler.
    Once the scheduler is running, this endpoint always returns a HIT.
    """
    key = f"{NAMESPACE}:trending"
    t0 = time.perf_counter()

    cached = await redis.get(key)
    if cached:
        _stats["cache_hits"] += 1
        ttl = await redis.ttl(key)
        return {
            "source": "cache",
            "cache_result": "HIT",
            "ttl_remaining_s": ttl,
            "products": json.loads(cached),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    _stats["cache_misses"] += 1
    trending = await _db_fetch_trending()
    _stats["db_hits"] += 1
    await redis.setex(key, TRENDING_TTL, json.dumps(trending))

    return {
        "source": "db",
        "cache_result": "MISS",
        "ttl_remaining_s": TRENDING_TTL,
        "products": trending,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@router.get("/config")
async def get_config(redis: aioredis.Redis = Depends(get_redis)):
    """
    Site config — pre-populated on startup, kept fresh by scheduler.
    Demonstrates startup warming: first request always hits the cache.
    """
    key = f"{NAMESPACE}:config"
    t0 = time.perf_counter()

    cached = await redis.get(key)
    if cached:
        _stats["cache_hits"] += 1
        ttl = await redis.ttl(key)
        return {
            "source": "cache",
            "cache_result": "HIT",
            "ttl_remaining_s": ttl,
            "config": json.loads(cached),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    _stats["cache_misses"] += 1
    config = await _db_fetch_config()
    _stats["db_hits"] += 1
    await redis.setex(key, CONFIG_TTL, json.dumps(config))

    return {
        "source": "db",
        "cache_result": "MISS",
        "config": config,
        "ttl_remaining_s": CONFIG_TTL,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@router.post("/warm/startup")
async def trigger_startup_warm(redis: aioredis.Redis = Depends(get_redis)):
    """
    Simulate application startup cache warming.
    Pre-populates all 20 products + config + trending.
    """
    result = await _do_startup_warm(redis)
    return result


@router.post("/warm/pipeline")
async def trigger_pipeline_warm(redis: aioredis.Redis = Depends(get_redis)):
    """
    Warm all 20 catalog products via a single Redis PIPELINE.
    All SET commands are batched into one TCP round-trip.
    """
    result = await _do_pipeline_warm(redis)
    return result


@router.post("/scheduler/start")
async def start_scheduler():
    """Start the background proactive-refresh scheduler."""
    global _scheduler_task, _scheduler_running

    if _scheduler_running and _scheduler_task and not _scheduler_task.done():
        return {
            "status": "already_running",
            "interval_s": SCHEDULER_INTERVAL,
            "refresh_count": _stats["scheduled_refresh_runs"],
        }

    _scheduler_running = True
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    _log("scheduler_start", f"Scheduler started — interval {SCHEDULER_INTERVAL} s")

    return {
        "status": "started",
        "interval_s": SCHEDULER_INTERVAL,
        "message": (
            f"Will refresh trending + config keys every {SCHEDULER_INTERVAL} s, "
            "before their TTLs expire."
        ),
    }


@router.post("/scheduler/stop")
async def stop_scheduler():
    """Stop the background proactive-refresh scheduler."""
    global _scheduler_task, _scheduler_running

    _scheduler_running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass

    _log("scheduler_stop", "Scheduler stopped")
    return {"status": "stopped"}


@router.get("/scheduler/status")
async def scheduler_status():
    """Return current scheduler state and next refresh countdown."""
    is_running = (
        _scheduler_running
        and _scheduler_task is not None
        and not _scheduler_task.done()
    )
    next_in: float | None = None
    if is_running and _last_scheduled_refresh:
        next_in = max(
            0.0, round(_last_scheduled_refresh + SCHEDULER_INTERVAL - time.time(), 1)
        )
    elif is_running:
        next_in = float(SCHEDULER_INTERVAL)

    return {
        "running": is_running,
        "interval_s": SCHEDULER_INTERVAL,
        "last_refresh_epoch": _last_scheduled_refresh,
        "next_refresh_in_s": next_in,
        "refresh_count": _stats["scheduled_refresh_runs"],
        "warm_log": _warm_log[-5:],
    }


@router.get("/cache/status")
async def cache_status(redis: aioredis.Redis = Depends(get_redis)):
    """Show all cw215 keys in Redis with their remaining TTLs."""
    pattern = f"{NAMESPACE}:*"
    keys = []
    async for key in redis.scan_iter(match=pattern, count=200):
        ttl = await redis.ttl(key)
        raw = await redis.get(key)
        size_bytes = len(raw.encode()) if raw else 0
        keys.append(
            {
                "key": key,
                "ttl_s": ttl,
                "size_bytes": size_bytes,
            }
        )
    keys.sort(key=lambda k: k["key"])
    return {
        "namespace": NAMESPACE,
        "total_keys": len(keys),
        "keys": keys,
    }


@router.delete("/cache/flush")
async def flush_cache(redis: aioredis.Redis = Depends(get_redis)):
    """Flush all cw215 keys from Redis (simulate cold cache / restart)."""
    pattern = f"{NAMESPACE}:*"
    deleted = 0
    async for key in redis.scan_iter(match=pattern, count=200):
        await redis.delete(key)
        deleted += 1
    _log("flush", f"Flushed {deleted} cw215 keys", deleted)
    return {"deleted": deleted, "namespace": NAMESPACE}


@router.get("/stats")
async def get_stats():
    """Return all hit/miss/warm counters plus the event log."""
    is_running = (
        _scheduler_running
        and _scheduler_task is not None
        and not _scheduler_task.done()
    )
    total = _stats["cache_hits"] + _stats["cache_misses"]
    hit_rate = round(_stats["cache_hits"] / total * 100, 1) if total else 0.0
    return {
        **_stats,
        "hit_rate_pct": hit_rate,
        "scheduler_running": is_running,
        "warm_log": _warm_log,
    }


@router.post("/stats/reset")
async def reset_stats(redis: aioredis.Redis = Depends(get_redis)):
    """Reset all counters, clear event log, and flush all cw215 cache keys."""
    for k in list(_stats.keys()):
        _stats[k] = 0
    _warm_log.clear()
    pattern = f"{NAMESPACE}:*"
    deleted = 0
    async for key in redis.scan_iter(match=pattern, count=200):
        await redis.delete(key)
        deleted += 1
    return {"reset": True, "cache_keys_deleted": deleted}
