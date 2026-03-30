"""Strategy 2.16 — Event-Driven Cache Invalidation
====================================================
Rather than relying solely on TTL expiry, push cache invalidation events via
Redis Pub/Sub exactly when data changes.  Two complementary patterns:

  1. Direct Key Invalidation  — when an entity is updated, publish a message
     containing the exact cache key.  Any subscriber (running on any app
     instance) receives it and immediately deletes that stale key.

  2. Tag-Based Invalidation   — cache keys are registered under "tag groups"
     stored as Redis SETs.  Publishing a tag name deletes *all* keys bearing
     that tag in one shot — great for purging a whole resource family after
     a bulk update (e.g., invalidate all "products" after a price import).

Redis conventions (namespace ei216):
  ei216:product:{id}   → cached product JSON     (TTL = PRODUCT_TTL  300 s)
  ei216:order:{id}     → cached order JSON        (TTL = ORDER_TTL    300 s)
  ei216:tags:{tag}     → SET of keys bearing this tag

Pub/Sub channels:
  cache:invalidate:ei216       → direct key invalidation
  cache:tags:invalidate:ei216  → tag-group invalidation
"""

import asyncio
import json
import os
import time
from collections import deque
from typing import Any

import redis.asyncio as aioredis
from core.redis_client import get_redis
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(
    prefix="/v1/events",
    tags=["2.16 Event-Driven Cache Invalidation"],
)

# ── Config ────────────────────────────────────────────────────────────────────
NAMESPACE = "ei216"
CHANNEL_KEY = "cache:invalidate:ei216"
CHANNEL_TAG = "cache:tags:invalidate:ei216"
PRODUCT_TTL = 300  # 5 min
ORDER_TTL = 300  # 5 min

# ── Simulated "database" ─────────────────────────────────────────────────────
_CATEGORIES = ["Electronics", "Books", "Clothing", "Food", "Home"]

_PRODUCTS: dict[int, dict] = {
    i: {
        "id": i,
        "name": f"Product {i:02d}",
        "category": _CATEGORIES[i % 5],
        "price": round(9.99 + i * 5.55, 2),
        "stock": i * 7 % 50 + 10,
        "updated_at": round(time.time(), 2),
    }
    for i in range(1, 11)
}

_ORDERS: dict[int, dict] = {
    i: {
        "id": i,
        "user_id": i % 3 + 1,
        "product_id": i % 10 + 1,
        "qty": i % 5 + 1,
        "status": ["pending", "confirmed", "shipped"][i % 3],
        "updated_at": round(time.time(), 2),
    }
    for i in range(1, 11)
}

# ── In-memory state ───────────────────────────────────────────────────────────
_stats: dict[str, int] = {
    "cache_hits": 0,
    "cache_misses": 0,
    "invalidations_published": 0,
    "invalidations_received": 0,
    "tag_invalidations": 0,
    "keys_deleted": 0,
}

_event_log: deque = deque(maxlen=30)  # last 30 invalidation events received
_listener_task: asyncio.Task | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _cache_set_with_tags(
    redis: aioredis.Redis, key: str, value: Any, ttl: int, tags: list[str]
) -> None:
    """Store a value and register the key under each tag SET for group invalidation."""
    pipe = redis.pipeline(transaction=False)
    pipe.setex(key, ttl, json.dumps(value))
    for tag in tags:
        tag_key = f"{NAMESPACE}:tags:{tag}"
        pipe.sadd(tag_key, key)
        pipe.expire(tag_key, ttl + 120)  # keep tag SET a bit longer than entries
    await pipe.execute()


async def _publish_key_invalidation(
    redis: aioredis.Redis, key: str, reason: str
) -> None:
    payload = json.dumps({"key": key, "reason": reason, "ts": round(time.time(), 3)})
    await redis.publish(CHANNEL_KEY, payload)
    _stats["invalidations_published"] += 1


async def _publish_tag_invalidation(
    redis: aioredis.Redis, tag: str, reason: str
) -> None:
    payload = json.dumps({"tag": tag, "reason": reason, "ts": round(time.time(), 3)})
    await redis.publish(CHANNEL_TAG, payload)
    _stats["invalidations_published"] += 1


# ── Pub/Sub background listener ───────────────────────────────────────────────


async def _invalidation_listener() -> None:
    """
    Long-running background coroutine.  Opens a *dedicated* Redis connection
    for Pub/Sub (subscribed connections cannot issue regular commands) and
    reacts to invalidation events on both channels.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    sub_client: aioredis.Redis = aioredis.from_url(
        redis_url, encoding="utf-8", decode_responses=True
    )
    pubsub = sub_client.pubsub()
    await pubsub.subscribe(CHANNEL_KEY, CHANNEL_TAG)
    print(f"[2.16] Pub/Sub listener subscribed → {CHANNEL_KEY}, {CHANNEL_TAG}")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            channel: str = message["channel"]
            try:
                data: dict = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            _stats["invalidations_received"] += 1
            log_entry: dict[str, Any] = {
                "ts": round(time.time(), 3),
                "channel": channel,
                "event_type": "key" if channel == CHANNEL_KEY else "tag",
                "data": data,
                "deleted": 0,
                "deleted_keys": [],
            }

            # Import lazily to avoid circular import at module load time
            from core import redis_client as _rc

            r: aioredis.Redis | None = _rc._redis
            if r is None:
                _event_log.append(log_entry)
                continue

            if channel == CHANNEL_KEY:
                key = data.get("key", "")
                if key:
                    deleted = await r.delete(key)
                    log_entry["deleted"] = deleted
                    _stats["keys_deleted"] += deleted

            elif channel == CHANNEL_TAG:
                tag = data.get("tag", "")
                if tag:
                    tag_key = f"{NAMESPACE}:tags:{tag}"
                    keys = await r.smembers(tag_key)
                    if keys:
                        deleted = await r.delete(*keys)
                        await r.delete(tag_key)
                        log_entry["deleted"] = deleted
                        log_entry["deleted_keys"] = sorted(keys)
                        _stats["keys_deleted"] += deleted
                    _stats["tag_invalidations"] += 1

            _event_log.append(log_entry)

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(CHANNEL_KEY, CHANNEL_TAG)
        await sub_client.aclose()
        print("[2.16] Pub/Sub listener stopped.")


# ── Endpoints — GET (cache-aside reads) ───────────────────────────────────────


@router.get("/product/{product_id}")
async def get_product(product_id: int, redis: aioredis.Redis = Depends(get_redis)):
    """Fetch a product.  Cache-aside with tag registration so the key can be
    bulk-invalidated via the 'products' or 'category:*' tags."""
    if product_id not in _PRODUCTS:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    cache_key = f"{NAMESPACE}:product:{product_id}"
    t0 = time.perf_counter()
    cached = await redis.get(cache_key)
    elapsed = (time.perf_counter() - t0) * 1000

    if cached:
        _stats["cache_hits"] += 1
        return {
            "data": json.loads(cached),
            "source": "cache",
            "cache_status": "HIT",
            "latency_ms": round(elapsed, 3),
            "cache_key": cache_key,
        }

    # Simulate slow DB read
    await asyncio.sleep(0.08)
    product = dict(_PRODUCTS[product_id])
    cat_tag = f"category:{product['category'].lower().replace(' ', '_')}"
    await _cache_set_with_tags(
        redis,
        cache_key,
        product,
        PRODUCT_TTL,
        tags=["products", cat_tag],
    )
    _stats["cache_misses"] += 1
    return {
        "data": product,
        "source": "database",
        "cache_status": "MISS",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 3),
        "cache_key": cache_key,
        "tags_registered": ["products", cat_tag],
    }


@router.get("/order/{order_id}")
async def get_order(order_id: int, redis: aioredis.Redis = Depends(get_redis)):
    """Fetch an order.  Cached with 'orders' and 'user:{uid}' tags."""
    if order_id not in _ORDERS:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

    cache_key = f"{NAMESPACE}:order:{order_id}"
    t0 = time.perf_counter()
    cached = await redis.get(cache_key)
    elapsed = (time.perf_counter() - t0) * 1000

    if cached:
        _stats["cache_hits"] += 1
        return {
            "data": json.loads(cached),
            "source": "cache",
            "cache_status": "HIT",
            "latency_ms": round(elapsed, 3),
            "cache_key": cache_key,
        }

    await asyncio.sleep(0.06)
    order = dict(_ORDERS[order_id])
    user_tag = f"user:{order['user_id']}"
    await _cache_set_with_tags(
        redis,
        cache_key,
        order,
        ORDER_TTL,
        tags=["orders", user_tag],
    )
    _stats["cache_misses"] += 1
    return {
        "data": order,
        "source": "database",
        "cache_status": "MISS",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 3),
        "cache_key": cache_key,
        "tags_registered": ["orders", user_tag],
    }


# ── Endpoints — writes (publish invalidation) ─────────────────────────────────


class ProductUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    stock: int | None = None


@router.put("/product/{product_id}")
async def update_product(
    product_id: int,
    update: ProductUpdate,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Update a product in the simulated DB, then publish a direct-key
    invalidation event.  The Pub/Sub listener receives it and deletes the
    stale cache entry automatically."""
    if product_id not in _PRODUCTS:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    p = _PRODUCTS[product_id]
    if update.name is not None:
        p["name"] = update.name
    if update.price is not None:
        p["price"] = round(float(update.price), 2)
    if update.stock is not None:
        p["stock"] = int(update.stock)
    p["updated_at"] = round(time.time(), 2)

    cache_key = f"{NAMESPACE}:product:{product_id}"
    await _publish_key_invalidation(redis, cache_key, "product_updated")

    return {
        "updated": dict(p),
        "invalidation": {
            "channel": CHANNEL_KEY,
            "key": cache_key,
            "published": True,
            "message": "Pub/Sub listener will delete the stale cache entry",
        },
    }


class OrderUpdate(BaseModel):
    status: str | None = None
    qty: int | None = None


@router.put("/order/{order_id}")
async def update_order(
    order_id: int,
    update: OrderUpdate,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Update an order in the simulated DB and publish a key invalidation event."""
    if order_id not in _ORDERS:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

    valid_statuses = {"pending", "confirmed", "shipped", "cancelled"}
    o = _ORDERS[order_id]
    if update.status is not None:
        if update.status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{update.status}'. Allowed: {sorted(valid_statuses)}",
            )
        o["status"] = update.status
    if update.qty is not None:
        o["qty"] = max(1, int(update.qty))
    o["updated_at"] = round(time.time(), 2)

    cache_key = f"{NAMESPACE}:order:{order_id}"
    await _publish_key_invalidation(redis, cache_key, "order_updated")

    return {
        "updated": dict(o),
        "invalidation": {
            "channel": CHANNEL_KEY,
            "key": cache_key,
            "published": True,
        },
    }


# ── Endpoints — manual invalidation ──────────────────────────────────────────


@router.post("/invalidate/key")
async def invalidate_key(
    key: str = Body(..., embed=True),
    reason: str = Body("manual", embed=True),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Publish a direct-key invalidation message to the Pub/Sub channel."""
    await _publish_key_invalidation(redis, key, reason)
    return {
        "published": True,
        "channel": CHANNEL_KEY,
        "key": key,
        "reason": reason,
    }


@router.post("/invalidate/tag/{tag}")
async def invalidate_by_tag(
    tag: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Publish a tag-group invalidation message.
    The listener will delete ALL cache keys registered under this tag."""
    # Peek at how many keys will be affected
    tag_key = f"{NAMESPACE}:tags:{tag}"
    members = await redis.smembers(tag_key)
    await _publish_tag_invalidation(redis, tag, "manual_tag_invalidation")
    return {
        "published": True,
        "channel": CHANNEL_TAG,
        "tag": tag,
        "affected_keys_preview": sorted(members),
    }


# ── Endpoints — inspection / monitoring ──────────────────────────────────────


@router.get("/keys/inspect")
async def inspect_keys(redis: aioredis.Redis = Depends(get_redis)):
    """Return all ei216:* keys with their TTLs, types, and tag memberships."""
    all_keys = await redis.keys(f"{NAMESPACE}:*")
    result: list[dict] = []
    for key in sorted(all_keys):
        ttl = await redis.ttl(key)
        key_type = await redis.type(key)
        entry: dict[str, Any] = {"key": key, "ttl": ttl, "type": key_type}
        if key.startswith(f"{NAMESPACE}:tags:"):
            members = await redis.smembers(key)
            entry["members"] = sorted(members)
        result.append(entry)
    return {"keys": result, "total": len(result)}


@router.get("/pubsub/events")
async def pubsub_events():
    """Return the last invalidation events received by the background listener."""
    return {
        "events": list(reversed(_event_log)),
        "total": len(_event_log),
        "listener_active": _listener_task is not None and not _listener_task.done(),
    }


@router.get("/stats")
async def get_stats():
    hit_total = _stats["cache_hits"] + _stats["cache_misses"]
    hit_rate = round(_stats["cache_hits"] / hit_total * 100, 1) if hit_total else 0
    return {
        "stats": dict(_stats),
        "hit_rate_pct": hit_rate,
        "listener_active": _listener_task is not None and not _listener_task.done(),
        "channels": {
            "key_invalidation": CHANNEL_KEY,
            "tag_invalidation": CHANNEL_TAG,
        },
    }


@router.post("/reset")
async def reset(redis: aioredis.Redis = Depends(get_redis)):
    """Flush all ei216:* keys, reset stats counter and event log."""
    keys = await redis.keys(f"{NAMESPACE}:*")
    deleted = 0
    if keys:
        deleted = await redis.delete(*keys)
    _stats.update({k: 0 for k in _stats})
    _event_log.clear()
    return {"flushed_keys": deleted, "stats_reset": True}
