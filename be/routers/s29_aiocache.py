"""
2.9 — Async Caching with aiocache (Advanced Patterns)
======================================================
Demonstrates features beyond the basic @cached decorator used in 2.6:

  • Direct cache instantiation — Cache(Cache.REDIS, serializer=..., plugins=[...])
  • MsgPackSerializer      — binary serialiser: 2–3× faster, 30–50% smaller than JSON
  • HitMissRatioPlugin     — built-in hit / miss tracking accumulates on the instance
  • TimingPlugin           — latency instrumentation on every cache operation
  • cache.multi_get()      — batch-fetch multiple keys in ONE Redis round-trip
  • cache.multi_set()      — batch-store DB results back to cache in ONE round-trip
  • cache.exists()         — check key presence without fetching the value
  • cache.delete()         — manual eviction of a single key
  • cache.clear()          — evict the entire ac29 namespace
  • Namespace isolation    — "ac29" prefix keeps these keys separate from 2.6's "rt:"
"""

import asyncio
import time
from typing import Any, Optional

from aiocache import Cache
from aiocache.plugins import HitMissRatioPlugin, TimingPlugin
from aiocache.serializers import MsgPackSerializer
from core.database import AsyncSessionLocal
from core.models import Recipe
from core.redis_client import AIOCACHE_HOST, AIOCACHE_PORT
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/v1/aiocache", tags=["2.9 Async aiocache"])

_AC_TTL = 180  # 3-minute TTL — visible in the demo
_NS = "ac29"  # namespace isolated from 2.6's "rt" namespace

# ── Cache instance ────────────────────────────────────────────────────────────
# Creating a cache instance directly avoids caches.set_config() entirely.
# The Redis connection pool is lazy — no socket is opened at import time.
# Passing the SAME instance to @cached and the endpoint code guarantees
# HitMissRatioPlugin counters accumulate correctly across all requests.
_cache = Cache(
    Cache.REDIS,
    endpoint=AIOCACHE_HOST,
    port=AIOCACHE_PORT,
    namespace=_NS,
    ttl=_AC_TTL,
    serializer=MsgPackSerializer(encoding=None),
    plugins=[HitMissRatioPlugin(), TimingPlugin()],
)

# ── Module-level counters ─────────────────────────────────────────────────────
_single_hits: int = 0
_single_misses: int = 0
_batch_hits: int = 0
_batch_misses: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _recipe_to_dict(row: Recipe) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "cuisine": row.cuisine,
        "prep_minutes": row.prep_minutes,
        "ingredients": row.ingredients,
        "instructions": row.instructions,
    }


async def _db_fetch_recipe(recipe_id: int) -> Optional[dict]:
    """Raw DB lookup — called only on a cache miss. Simulates 140 ms latency."""
    await asyncio.sleep(0.14)
    async with AsyncSessionLocal() as session:
        row = await session.get(Recipe, recipe_id)
        return _recipe_to_dict(row) if row else None


# ── Read-through helper — MsgPack + plugin stack ─────────────────────────────
# Using _cache.get()/_cache.set() directly keeps the SAME instance (and its
# plugins/serialiser) consistent across the single, batch, and evict endpoints.
async def _cached_fetch_recipe(recipe_id: int) -> Optional[dict]:
    """
    Read-through: check cache first, fall back to DB on miss.
    The value is serialised with MsgPack before being stored in Redis.
    """
    key = str(recipe_id)
    data = await _cache.get(key)
    if data is None:
        data = await _db_fetch_recipe(recipe_id)
        if data is not None:
            await _cache.set(key, data, ttl=_AC_TTL)
    return data


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/recipe/{recipe_id}")
async def get_recipe(recipe_id: int):
    """
    Fetch a single recipe via @cached + MsgPackSerializer.

    Flow:
      HIT  → aiocache reads from Redis, MsgPack-deserialises (~1 ms) — DB never called.
      MISS → _cached_fetch_recipe body runs (140 ms DB), result serialised with
             MsgPack and stored; served from cache on next call.

    The response includes live HitMissRatioPlugin stats from the cache instance.
    """
    global _single_hits, _single_misses

    if not 1 <= recipe_id <= 8:
        raise HTTPException(
            status_code=404,
            detail=f"No recipe with id={recipe_id} (valid range: 1–8)",
        )

    # Check existence BEFORE calling @cached so we can label hit/miss accurately.
    # _cache.exists() uses the configured namespace automatically.
    try:
        pre_exists = await _cache.exists(str(recipe_id))
    except Exception:
        pre_exists = False

    t0 = time.perf_counter()
    data = await _cached_fetch_recipe(recipe_id)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    if data is None:
        raise HTTPException(
            status_code=404, detail=f"Recipe {recipe_id} not found in DB"
        )

    if pre_exists:
        _single_hits += 1
    else:
        _single_misses += 1

    # HitMissRatioPlugin sets hit_miss_ratio on the cache instance after each get.
    plugin_ratio = getattr(
        _cache, "hit_miss_ratio", {"hits": 0, "misses": 0, "hit_ratio": 0}
    )

    return {
        "data": data,
        "cache_hit": bool(pre_exists),
        "source": "redis_msgpack" if pre_exists else "sqlite_db",
        "latency_ms": latency_ms,
        "cache_key": f"{_NS}:{recipe_id}",
        "ttl_seconds": _AC_TTL,
        "serializer": "MsgPackSerializer",
        "plugin_ratio": plugin_ratio,
        "pattern": "aiocache-cached-msgpack",
        "note": (
            "MsgPack is a binary format: 2–3× faster to serialise than JSON "
            "and produces 30–50% smaller payloads — measurable at scale."
        ),
    }


@router.get("/recipes")
async def batch_recipes(ids: str = "1,2,3,4"):
    """
    Fetch multiple recipes in a SINGLE Redis round-trip via cache.multi_get().

    HIT keys  → returned immediately from Redis (de-serialised with MsgPack).
    MISS keys → fetched from DB in parallel with asyncio.gather(), then stored
                back via cache.multi_set() — again ONE round-trip.

    The response shows per-ID hit/miss attribution and compares the actual
    multi_get latency against the estimated cost of N individual GETs.
    """
    global _batch_hits, _batch_misses

    id_list: list[int] = []
    for x in ids.split(","):
        x = x.strip()
        if x.isdigit() and 1 <= int(x) <= 8:
            id_list.append(int(x))

    if not id_list:
        raise HTTPException(
            status_code=400,
            detail="Provide valid recipe IDs (1–8) as ?ids=1,2,3",
        )

    keys = [str(i) for i in id_list]

    # ── ONE round-trip to Redis for all requested IDs ─────────────────────────
    t_start = time.perf_counter()
    cached_values = await _cache.multi_get(keys)
    multi_get_ms = round((time.perf_counter() - t_start) * 1000, 2)

    hit_ids: list[int] = []
    miss_ids: list[int] = []
    results: dict[int, Any] = {}

    for recipe_id, val in zip(id_list, cached_values):
        if val is not None:
            hit_ids.append(recipe_id)
            results[recipe_id] = val
        else:
            miss_ids.append(recipe_id)

    # ── Fetch DB misses concurrently ──────────────────────────────────────────
    if miss_ids:
        fetched = await asyncio.gather(*[_db_fetch_recipe(rid) for rid in miss_ids])
        db_pairs: list[tuple[str, Any]] = []
        for rid, d in zip(miss_ids, fetched):
            if d is not None:
                results[rid] = d
                db_pairs.append((str(rid), d))

        # ── ONE round-trip to store all misses back into cache ────────────────
        if db_pairs:
            await _cache.multi_set(db_pairs, ttl=_AC_TTL)

    total_ms = round((time.perf_counter() - t_start) * 1000, 2)
    _batch_hits += len(hit_ids)
    _batch_misses += len(miss_ids)

    return {
        "recipes": [results[rid] for rid in id_list if rid in results],
        "total_returned": len(results),
        "ids_requested": id_list,
        "cache_hits": hit_ids,
        "cache_misses": miss_ids,
        "hit_count": len(hit_ids),
        "miss_count": len(miss_ids),
        "redis_round_trips": 1,
        "multi_get_ms": multi_get_ms,
        "total_ms": total_ms,
        "estimated_individual_gets_ms": round(multi_get_ms * len(id_list), 1),
        "serializer": "MsgPackSerializer",
        "note": (
            "multi_get() issues a single Redis MGET — latency is constant "
            "regardless of how many IDs you request."
        ),
    }


@router.delete("/recipe/{recipe_id}")
async def evict_recipe(recipe_id: int):
    """Manually delete a single recipe key from the ac29 namespace."""
    if not 1 <= recipe_id <= 8:
        raise HTTPException(
            status_code=404,
            detail=f"No recipe with id={recipe_id} (valid range: 1–8)",
        )
    deleted = await _cache.delete(str(recipe_id))
    return {
        "evicted": bool(deleted),
        "key": f"{_NS}:{recipe_id}",
        "next_get": "MISS — DB will be queried; result re-serialised with MsgPack and stored",
    }


@router.delete("/cache")
async def clear_namespace():
    """Evict every key in the ac29 namespace (equivalent to Redis KEYS ac29:* + DEL)."""
    await _cache.clear()
    return {
        "cleared": True,
        "namespace": _NS,
        "note": "All 8 recipe keys removed. Next fetches will all be MISS.",
    }


@router.get("/stats")
async def aiocache_stats():
    """
    Return live stats from:
      • Manual counters (single-endpoint vs batch-endpoint hits/misses)
      • HitMissRatioPlugin — stores hit_miss_ratio on the cache instance after each get
      • Full cache configuration for educational display
    """
    # HitMissRatioPlugin sets this dict on the cache client after the first get operation.
    plugin_ratio = getattr(
        _cache, "hit_miss_ratio", {"hits": 0, "misses": 0, "hit_ratio": 0}
    )

    total_single = _single_hits + _single_misses
    total_batch = _batch_hits + _batch_misses
    total_all = total_single + total_batch
    total_hits = _single_hits + _batch_hits
    total_misses = _single_misses + _batch_misses

    return {
        "single_endpoint": {
            "hits": _single_hits,
            "misses": _single_misses,
            "total": total_single,
            "hit_ratio_pct": (
                round(_single_hits / total_single * 100, 1) if total_single else 0
            ),
        },
        "batch_endpoint": {
            "hits": _batch_hits,
            "misses": _batch_misses,
            "total": total_batch,
            "hit_ratio_pct": (
                round(_batch_hits / total_batch * 100, 1) if total_batch else 0
            ),
        },
        "combined": {
            "hits": total_hits,
            "misses": total_misses,
            "total": total_all,
            "hit_ratio_pct": round(total_hits / total_all * 100, 1) if total_all else 0,
        },
        "aiocache_plugin": {
            "HitMissRatioPlugin": plugin_ratio,
            "note": "Accumulated on the shared RedisCache instance — never resets on restart unless cleared",
        },
        "config": {
            "namespace": _NS,
            "ttl_seconds": _AC_TTL,
            "serializer": "MsgPackSerializer",
            "plugins": ["HitMissRatioPlugin", "TimingPlugin"],
            "backend": "aiocache.RedisCache",
            "endpoint": AIOCACHE_HOST,
            "port": AIOCACHE_PORT,
        },
    }


@router.post("/reset")
async def reset_stats():
    """Reset manual counters and the HitMissRatioPlugin state on the cache instance."""
    global _single_hits, _single_misses, _batch_hits, _batch_misses
    _single_hits = _single_misses = _batch_hits = _batch_misses = 0
    if hasattr(_cache, "hit_miss_ratio"):
        _cache.hit_miss_ratio = {"hits": 0, "misses": 0, "hit_ratio": 0}
    return {"reset": True, "message": "All counters and plugin stats reset to zero"}
