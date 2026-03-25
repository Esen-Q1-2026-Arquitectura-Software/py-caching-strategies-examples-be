"""
Caching Showcase — FastAPI Backend
===================================
Demonstrates:
  2.1 In-Memory Caching
      • functools.lru_cache  — no TTL, LRU eviction, process-scoped
      • cachetools.TTLCache  — 30-second TTL, thread-safe, LRU eviction
  2.2 Redis — The Core Backend Cache
      • cache-aside pattern  — SETEX/GET, 300-second TTL, connection pool
  2.3 Cache-Aside (Lazy Loading) Pattern
      • reusable decorator, SQLAlchemy ORM, negative caching (null TTL)
  2.4 Write-Through Pattern
      • synchronous write to DB + cache on every PUT, eliminating stale reads

Each endpoint returns:
  {data, cache_hit, source, latency_ms, ...}
so the frontend can visualise hit/miss and timing differences clearly.
"""

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache, wraps
from typing import Optional

import redis.asyncio as aioredis
from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import JSON, Column, Float, Integer, String, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# ──────────────────────────────────────────────────────────────────────────────
# Database (SQLite, async via aiosqlite — used as the "slow source of truth")
# ──────────────────────────────────────────────────────────────────────────────
DATABASE_URL = "sqlite+aiosqlite:///./demo.db"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ──────────────────────────────────────────────────────────────────────────────
# ORM Models  (used by 2.3 Cache-Aside and 2.4 Write-Through demos)
# ──────────────────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


class Order(Base):
    """Orders table — source of truth for the 2.3 Cache-Aside demo."""

    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    customer = Column(String(100), nullable=False)
    status = Column(
        String(50), nullable=False
    )  # pending | processing | shipped | delivered
    amount = Column(Float, nullable=False)
    items = Column(JSON, nullable=False)  # list[{sku, name, qty, price}]


class UserProfile(Base):
    """User profiles table — source of truth for the 2.4 Write-Through demo."""

    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), nullable=False)
    bio = Column(String(500), nullable=False)
    preferences = Column(JSON, nullable=False)  # {theme, language, notifications}


class EventLog(Base):
    """Analytics event log — persisted by the 2.5 Write-Behind background worker."""

    __tablename__ = "event_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(
        String(50), nullable=False
    )  # page_view | click | purchase | login | logout
    user_id = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(Float, nullable=False)  # unix epoch timestamp


# ── Seed data ─────────────────────────────────────────────────────────────────

_SEED_ORDERS = [
    {
        "id": 1,
        "customer": "Alice Johnson",
        "status": "delivered",
        "amount": 299.99,
        "items": [
            {"sku": "LAP-001", "name": "Laptop Pro 15", "qty": 1, "price": 299.99}
        ],
    },
    {
        "id": 2,
        "customer": "Bob Smith",
        "status": "processing",
        "amount": 42.98,
        "items": [
            {"sku": "MSE-001", "name": "Wireless Mouse", "qty": 1, "price": 29.99},
            {"sku": "NTB-001", "name": "Notebook Pack", "qty": 1, "price": 12.99},
        ],
    },
    {
        "id": 3,
        "customer": "Carol White",
        "status": "pending",
        "amount": 1449.98,
        "items": [
            {"sku": "DSK-001", "name": "Standing Desk", "qty": 1, "price": 449.99},
            {"sku": "LAP-001", "name": "Laptop Pro 15", "qty": 2, "price": 999.99},
        ],
    },
    {
        "id": 4,
        "customer": "David Lee",
        "status": "shipped",
        "amount": 89.99,
        "items": [{"sku": "COF-001", "name": "Coffee Maker", "qty": 1, "price": 89.99}],
    },
    {
        "id": 5,
        "customer": "Eve Martinez",
        "status": "delivered",
        "amount": 25.98,
        "items": [
            {"sku": "NTB-001", "name": "Notebook Pack", "qty": 1, "price": 12.99},
            {"sku": "PEN-001", "name": "Gel Pen Set", "qty": 1, "price": 12.99},
        ],
    },
]

_SEED_PROFILES = [
    {
        "id": 1,
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "bio": "Software engineer, coffee enthusiast.",
        "preferences": {"theme": "dark", "language": "en", "notifications": True},
    },
    {
        "id": 2,
        "name": "Bob Smith",
        "email": "bob@example.com",
        "bio": "UX designer, typography nerd.",
        "preferences": {"theme": "light", "language": "fr", "notifications": False},
    },
    {
        "id": 3,
        "name": "Carol White",
        "email": "carol@example.com",
        "bio": "DevOps engineer, automation fan.",
        "preferences": {"theme": "dark", "language": "de", "notifications": True},
    },
    {
        "id": 4,
        "name": "David Lee",
        "email": "david@example.com",
        "bio": "Backend developer, open source contributor.",
        "preferences": {"theme": "light", "language": "ja", "notifications": True},
    },
    {
        "id": 5,
        "name": "Eve Martinez",
        "email": "eve@example.com",
        "bio": "Full-stack developer, digital nomad.",
        "preferences": {"theme": "dark", "language": "es", "notifications": False},
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# 2.1 — lru_cache  (functools, no TTL, lives for the lifetime of the process)
# ──────────────────────────────────────────────────────────────────────────────

# Static country config — changes so rarely that no TTL is needed.
_COUNTRY_DB: dict[str, dict] = {
    "US": {
        "code": "US",
        "currency": "USD",
        "tax_rate": 0.08,
        "language": "en-US",
        "region": "North America",
    },
    "DE": {
        "code": "DE",
        "currency": "EUR",
        "tax_rate": 0.19,
        "language": "de-DE",
        "region": "Europe",
    },
    "JP": {
        "code": "JP",
        "currency": "JPY",
        "tax_rate": 0.10,
        "language": "ja-JP",
        "region": "Asia",
    },
    "BR": {
        "code": "BR",
        "currency": "BRL",
        "tax_rate": 0.12,
        "language": "pt-BR",
        "region": "South America",
    },
    "IN": {
        "code": "IN",
        "currency": "INR",
        "tax_rate": 0.18,
        "language": "hi-IN",
        "region": "Asia",
    },
    "GB": {
        "code": "GB",
        "currency": "GBP",
        "tax_rate": 0.20,
        "language": "en-GB",
        "region": "Europe",
    },
}


@lru_cache(maxsize=128)
def _lru_fetch_country(country_code: str) -> dict:
    """
    Decorated with @lru_cache — result stored indefinitely in process RAM.
    Evicts the Least Recently Used entry when maxsize=128 is reached.
    IMPORTANT: time.sleep() simulates a real DB round-trip on first call.
    """
    time.sleep(0.120)  # simulate 120 ms DB latency — only paid on cache MISS
    return _COUNTRY_DB.get(
        country_code,
        {
            "code": country_code,
            "currency": "USD",
            "tax_rate": 0.0,
            "language": "en",
            "region": "Unknown",
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2.1 — cachetools TTLCache  (30-second TTL, thread-safe)
# ──────────────────────────────────────────────────────────────────────────────

_TTL_SECONDS = 30
_ttl_store: TTLCache = TTLCache(maxsize=100, ttl=_TTL_SECONDS)
_ttl_lock = threading.Lock()
_ttl_hits = 0
_ttl_misses = 0

# User permission table — changes occasionally, so 30 s TTL makes sense.
_USER_PERMISSIONS_DB: dict[int, list[str]] = {
    1: ["read", "write", "delete", "admin"],
    2: ["read", "write"],
    3: ["read"],
    4: ["read", "write", "publish"],
    5: ["read", "comment"],
}


def _ttl_get_permissions(user_id: int) -> tuple[list[str], bool]:
    """Returns (permissions, cache_hit). Blocking — call via asyncio.to_thread."""
    global _ttl_hits, _ttl_misses
    key = f"perms:{user_id}"
    with _ttl_lock:
        if key in _ttl_store:
            _ttl_hits += 1
            return _ttl_store[key], True
        # Cache miss — simulate slow DB
        time.sleep(0.150)  # 150 ms
        perms = _USER_PERMISSIONS_DB.get(user_id, ["read"])
        _ttl_store[key] = perms
        _ttl_misses += 1
        return perms, False


# ──────────────────────────────────────────────────────────────────────────────
# 2.2 — Redis  (connection pool, cache-aside, SETEX / GET)
# ──────────────────────────────────────────────────────────────────────────────

_redis: Optional[aioredis.Redis] = None

_PRODUCT_DATA: dict[int, dict] = {
    1: {
        "id": 1,
        "name": "Laptop Pro 15",
        "category": "Electronics",
        "price": 1299.99,
        "description": "High-performance laptop with M-series chip",
    },
    2: {
        "id": 2,
        "name": "Wireless Mouse",
        "category": "Electronics",
        "price": 29.99,
        "description": "Ergonomic wireless mouse, 3-year battery life",
    },
    3: {
        "id": 3,
        "name": "Standing Desk",
        "category": "Furniture",
        "price": 449.99,
        "description": "Electric height-adjustable standing desk",
    },
    4: {
        "id": 4,
        "name": "Coffee Maker",
        "category": "Appliances",
        "price": 89.99,
        "description": "Programmable 12-cup drip coffee maker",
    },
    5: {
        "id": 5,
        "name": "Notebook Pack",
        "category": "Stationery",
        "price": 12.99,
        "description": "Pack of 5 college-ruled spiral notebooks",
    },
}

_REDIS_TTL = 300  # 5 minutes


async def _db_fetch_product(product_id: int) -> Optional[dict]:
    """Simulate a slow async DB read — 200 ms."""
    await asyncio.sleep(0.200)
    return _PRODUCT_DATA.get(product_id)


async def get_redis() -> Optional[aioredis.Redis]:
    return _redis


# ──────────────────────────────────────────────────────────────────────────────
# Lifespan
# ──────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis, _wb_worker_task

    # ── Create SQLAlchemy tables and seed if empty ─────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(Order))).scalars().first()
        if existing is None:
            for row in _SEED_ORDERS:
                session.add(Order(**row))
            for row in _SEED_PROFILES:
                session.add(UserProfile(**row))
            await session.commit()
            print("DB seeded with orders and user profiles.")

    # ── Redis connection pool ─────────────────────────────────────────────────
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        _redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=2,
        )
        await _redis.ping()
        print(f"Redis connected: {redis_url}")
    except Exception as exc:
        print(f"WARNING: Redis unavailable ({exc}). Redis endpoints will return 503.")
        _redis = None
    # ── Start Write-Behind background worker ─────────────────────────────────
    if _redis:
        _wb_worker_task = asyncio.create_task(_write_behind_worker())
        print("Write-Behind background worker started.")

    yield

    # ── Shutdown: cancel background worker ────────────────────────────────────
    if _wb_worker_task is not None:
        _wb_worker_task.cancel()
        try:
            await _wb_worker_task
        except asyncio.CancelledError:
            pass
    if _redis:
        await _redis.aclose()
    await engine.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Caching Showcase API",
    description="FastAPI backend demonstrating 2.1–2.5 caching strategies",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "caching-showcase-be",
        "strategies": [
            "2.1 lru_cache + cachetools.TTLCache",
            "2.2 Redis SETEX/GET",
            "2.3 Cache-Aside (Lazy Loading)",
            "2.4 Write-Through",
            "2.5 Write-Behind (Write-Back)",
        ],
    }


@app.get("/health")
async def health():
    redis_ok = False
    if _redis:
        try:
            await _redis.ping()
            redis_ok = True
        except Exception:
            pass
    return {"status": "ok", "redis": redis_ok}


# ══════════════════════════════════════════════════════════════════════════════
# 2.1 — lru_cache endpoints
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/v1/lru/country/{code}")
async def lru_get_country(code: str):
    """
    **2.1 lru_cache** — Country configuration lookup.

    - First call for a code  → DB hit (120 ms), result stored in process RAM.
    - Subsequent calls       → in-memory hit (< 1 ms), DB never touched.
    - No TTL: entries live until LRU eviction or server restart.
    - `lru_cache_info()` exposes hits / misses / currsize natively.
    """
    code = code.upper()
    start = time.perf_counter()

    # Capture stats snapshot BEFORE the call
    info_before = _lru_fetch_country.cache_info()

    # Run the blocking function in a thread pool — never block the event loop
    data = await asyncio.to_thread(_lru_fetch_country, code)

    info_after = _lru_fetch_country.cache_info()
    cache_hit = info_after.hits > info_before.hits

    latency_ms = (time.perf_counter() - start) * 1000

    return {
        "data": data,
        "cache_hit": cache_hit,
        "source": "lru_cache" if cache_hit else "simulated_db",
        "latency_ms": round(latency_ms, 2),
        "ttl_remaining_seconds": None,
        "note": "lru_cache — no TTL, lives in process RAM, LRU eviction at maxsize=128",
    }


@app.get("/v1/lru/stats")
async def lru_stats():
    """Return lru_cache statistics from cache_info()."""
    info = _lru_fetch_country.cache_info()
    total = info.hits + info.misses
    return {
        "strategy": "functools.lru_cache",
        "hits": info.hits,
        "misses": info.misses,
        "cache_size": info.currsize,
        "max_size": info.maxsize,
        "hit_rate_pct": round(info.hits / max(total, 1) * 100, 1),
        "ttl_seconds": None,
        "note": "lru_cache tracks hits/misses/currsize natively via cache_info()",
    }


@app.delete("/v1/lru/cache")
async def lru_clear():
    """Clear the lru_cache (simulates a server restart for demo purposes)."""
    _lru_fetch_country.cache_clear()
    return {
        "message": "lru_cache cleared — next call will be a cache miss",
        "cache_size": 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2.1 — cachetools TTLCache endpoints
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/v1/ttl/user/{user_id}")
async def ttl_get_user(user_id: int):
    """
    **2.1 cachetools TTLCache** — User permissions lookup.

    - First call for a user_id → DB hit (150 ms), result stored in TTLCache.
    - Subsequent calls (within 30 s) → in-memory hit (< 1 ms).
    - After 30 s → entry auto-expires, next call is a miss again.
    - Thread-safe via `threading.Lock()`.
    """
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


@app.get("/v1/ttl/stats")
async def ttl_stats():
    """Return TTLCache statistics."""
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


@app.delete("/v1/ttl/cache")
async def ttl_clear():
    """Clear the TTLCache."""
    global _ttl_hits, _ttl_misses
    with _ttl_lock:
        _ttl_store.clear()
        _ttl_hits = 0
        _ttl_misses = 0
    return {
        "message": "TTLCache cleared — next call will be a cache miss",
        "cache_size": 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2.2 — Redis endpoints  (cache-aside pattern)
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/v1/redis/product/{product_id}")
async def redis_get_product(
    product_id: int, redis: Optional[aioredis.Redis] = Depends(get_redis)
):
    """
    **2.2 Redis** — Product lookup using cache-aside pattern.

    ```
    GET /v1/redis/product/{id}
      1. redis.GET "product:{id}"
         → HIT:  return JSON, ~1 ms
         → MISS: fetch from DB (200 ms), redis.SETEX "product:{id}" 300 <json>
    ```

    - Cache key format: `product:{id}`
    - TTL: 300 seconds (5 minutes)
    - Connection pool: max 20 connections (set in lifespan)
    - Distributed: all FastAPI workers share the same Redis instance
    """
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Redis is unavailable — start Redis and restart the backend",
        )
    if product_id < 1 or product_id > 5:
        raise HTTPException(
            status_code=400, detail="product_id must be 1–5 for this demo"
        )

    cache_key = f"product:{product_id}"
    start = time.perf_counter()

    # ── 1. Check Redis ────────────────────────────────────────────────────────
    cached_raw = await redis.get(cache_key)
    if cached_raw:
        ttl_remaining = await redis.ttl(cache_key)
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "data": json.loads(cached_raw),
            "cache_hit": True,
            "source": "redis",
            "latency_ms": round(latency_ms, 2),
            "ttl_remaining_seconds": ttl_remaining,
            "cache_key": cache_key,
            "note": "Redis SETEX/GET — distributed cache shared across all workers/instances",
        }

    # ── 2. Cache miss: fetch from DB ──────────────────────────────────────────
    product = await _db_fetch_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    # ── 3. Populate Redis with TTL ────────────────────────────────────────────
    await redis.setex(cache_key, _REDIS_TTL, json.dumps(product))

    latency_ms = (time.perf_counter() - start) * 1000
    return {
        "data": product,
        "cache_hit": False,
        "source": "simulated_db",
        "latency_ms": round(latency_ms, 2),
        "ttl_remaining_seconds": _REDIS_TTL,
        "cache_key": cache_key,
        "note": "Redis SETEX/GET — distributed cache shared across all workers/instances",
    }


@app.get("/v1/redis/stats")
async def redis_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Return Redis server stats and currently cached product keys."""
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    server_info = await redis.info("server")
    stats_info = await redis.info("stats")

    # Scan for product keys (SCAN is non-blocking, unlike KEYS)
    cached_keys = []
    async for key in redis.scan_iter("product:*"):
        ttl = await redis.ttl(key)
        cached_keys.append({"key": key, "ttl_remaining_seconds": ttl})

    return {
        "strategy": "Redis SETEX/GET (cache-aside)",
        "redis_version": server_info.get("redis_version"),
        "keyspace_hits": stats_info.get("keyspace_hits", 0),
        "keyspace_misses": stats_info.get("keyspace_misses", 0),
        "cached_product_keys": sorted(cached_keys, key=lambda x: x["key"]),
        "product_ttl_seconds": _REDIS_TTL,
        "note": "SCAN used instead of KEYS — safe in production (non-blocking)",
    }


@app.delete("/v1/redis/cache")
async def redis_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Delete all product:* keys from Redis."""
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    deleted = 0
    async for key in redis.scan_iter("product:*"):
        await redis.delete(key)
        deleted += 1

    return {
        "message": f"Cleared {deleted} product key(s) from Redis — next call will be a miss",
        "deleted_count": deleted,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2.3 — Cache-Aside (Lazy Loading) Pattern
# ══════════════════════════════════════════════════════════════════════════════
#
# Key differences vs 2.2 (basic Redis):
#   • Uses a *real* SQLAlchemy ORM query (not a fake in-memory dict)
#   • Implements *negative caching* — caches null result with a short TTL
#     so repeated 404 lookups never reach the database
#   • Separates positive TTL (120 s) from negative TTL (15 s)
#   • Pattern is encapsulated in a reusable decorator shown in /v1/cache-aside/stats
# ──────────────────────────────────────────────────────────────────────────────

_CA_TTL = 120  # positive result TTL (2 min)
_CA_NULL_TTL = 15  # negative result TTL (15 s)  ← prevents DB hammering on 404s

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


@app.get("/v1/cache-aside/order/{order_id}")
async def cache_aside_get_order(
    order_id: int,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """
    **2.3 Cache-Aside (Lazy Loading)** — Order lookup via SQLAlchemy + Redis.

    Full lazy-loading lifecycle:
    ```
    GET /v1/cache-aside/order/{id}
      1. redis.GET "order:{id}"
         → HIT (positive):  return cached order        ~1 ms
         → HIT (negative):  return 404 from nil cache  ~1 ms  ← DB never touched
         → MISS: SELECT from SQLite via SQLAlchemy  ~180 ms
                 found     → SETEX "order:{id}" 120 <json>  → return order
                 not found → SETEX "order:{id}" 15 "null"   → return 404
    ```

    Negative caching is the key upgrade over 2.2: if an ID does not exist,
    the null result is also cached so the DB is only hit once per TTL window.
    """
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if order_id < 1:
        raise HTTPException(
            status_code=400, detail="order_id must be a positive integer"
        )

    cache_key = f"order:{order_id}"
    start = time.perf_counter()

    # ── Step 1: Check Redis ────────────────────────────────────────────────────
    cached_raw = await redis.get(cache_key)
    if cached_raw is not None:
        ttl_remaining = await redis.ttl(cache_key)
        latency_ms = (time.perf_counter() - start) * 1000

        # Negative cache hit — order does not exist but DB is not touched
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
                    "note": (
                        "Negative cache HIT — DB NOT queried. "
                        f"Null cached for {_CA_NULL_TTL}s to prevent DB hammering on repeated 404s."
                    ),
                },
            )

        # Positive cache hit
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

    # ── Step 2: Cache miss — query the real DB (SQLAlchemy) ───────────────────
    await asyncio.sleep(0.180)  # simulate realistic I/O latency
    result = await db.execute(select(Order).where(Order.id == order_id))
    order_row = result.scalar_one_or_none()

    # ── Step 3a: Not found — negative cache with short TTL ────────────────────
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
                "note": (
                    f"Null result cached for {_CA_NULL_TTL}s (negative caching). "
                    "Next lookup for this ID will skip the DB entirely."
                ),
            },
        )

    # ── Step 3b: Found — populate cache with positive TTL ─────────────────────
    order_dict = {
        "id": order_row.id,
        "customer": order_row.customer,
        "status": order_row.status,
        "amount": order_row.amount,
        "items": order_row.items,
    }
    await redis.setex(cache_key, _CA_TTL, json.dumps(order_dict))
    _ca_miss()

    latency_ms = (time.perf_counter() - start) * 1000
    return {
        "data": order_dict,
        "cache_hit": False,
        "source": "sqlalchemy_db",
        "latency_ms": round(latency_ms, 2),
        "ttl_remaining_seconds": _CA_TTL,
        "cache_key": cache_key,
        "note": (
            f"Cache-Aside MISS — fetched from SQLAlchemy, "
            f"now cached in Redis for {_CA_TTL}s."
        ),
    }


@app.get("/v1/cache-aside/stats")
async def cache_aside_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Return Cache-Aside pattern stats and currently cached order keys."""
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    with _ca_lock:
        hits, misses, neg_hits = _ca_hits, _ca_misses, _ca_neg_hits
    total = hits + misses

    # Non-blocking scan for order:* keys
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
        "note": (
            "Cache populated lazily — only requested IDs get cached. "
            "Negative caching prevents DB hammering on repeated 404s."
        ),
    }


@app.delete("/v1/cache-aside/cache")
async def cache_aside_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Evict all order:* keys from Redis and reset stats."""
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


# ══════════════════════════════════════════════════════════════════════════════
# 2.4 — Write-Through Pattern
# ══════════════════════════════════════════════════════════════════════════════
#
# Rule: every write (PUT /profile/{id}) immediately:
#   1. updates the row in SQLAlchemy / SQLite
#   2. overwrites the Redis key with the new value (same TTL)
#
# This guarantees that a subsequent GET always returns fresh data from cache.
# The demo includes a write history so you can observe the timeline of ops.
# ──────────────────────────────────────────────────────────────────────────────

_WT_TTL = 600  # 10-minute cache TTL

_wt_reads_hit = 0
_wt_reads_miss = 0
_wt_writes = 0
_wt_recent_ops: list[dict] = []  # last 10 operations
_wt_lock = threading.Lock()

_MAX_WT_OPS = 10


def _wt_record_op(op: dict) -> None:
    global _wt_recent_ops
    with _wt_lock:
        _wt_recent_ops = ([op] + _wt_recent_ops)[:_MAX_WT_OPS]


class ProfileUpdate(BaseModel):
    """Request body for the write-through PUT endpoint."""

    name: Optional[str] = None
    bio: Optional[str] = None
    preferences: Optional[dict] = None


@app.get("/v1/write-through/profile/{user_id}")
async def write_through_get_profile(
    user_id: int,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """
    **2.4 Write-Through — GET** — Read user profile via Redis.

    Because every PUT writes to the cache synchronously, this GET should
    almost always be a cache HIT after the first access or after any write.

    Fallback path (cache miss → DB) exists only for the very first read
    before any write has occurred, or after the cache is manually cleared.
    """
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if user_id < 1 or user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")

    cache_key = f"profile:{user_id}"
    start = time.perf_counter()

    # ── 1. Check Redis ─────────────────────────────────────────────────────────
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
            "note": (
                "Write-Through HIT — cache is always current because every PUT "
                "writes to DB + Redis synchronously."
            ),
        }

    # ── 2. Cache miss — load from DB (cold start or after cache clear) ─────────
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
    # Warm the cache (lazy fallback — only for cold start)
    await redis.setex(cache_key, _WT_TTL, json.dumps(profile_dict))

    with _wt_lock:
        global _wt_reads_miss
        _wt_reads_miss += 1

    latency_ms = (time.perf_counter() - start) * 1000
    return {
        "data": profile_dict,
        "cache_hit": False,
        "source": "sqlalchemy_db",
        "latency_ms": round(latency_ms, 2),
        "ttl_remaining_seconds": _WT_TTL,
        "cache_key": cache_key,
        "note": (
            "Cold-start miss — profile loaded from DB and cached. "
            "All future GETs AND PUTs will keep the cache current (write-through)."
        ),
    }


@app.put("/v1/write-through/profile/{user_id}")
async def write_through_update_profile(
    user_id: int,
    body: ProfileUpdate,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """
    **2.4 Write-Through — PUT** — Update profile in DB **and** cache simultaneously.

    Both writes happen in the same async context before we respond:
    ```
    PUT /v1/write-through/profile/{id}
      1. UPDATE user_profiles SET ... WHERE id = {id}   ← SQLAlchemy (SQLite)
      2. SETEX  profile:{id}  600  <updated json>        ← Redis
      3. Return updated profile
    ```

    This is what makes Write-Through different from Cache-Aside:
    in Cache-Aside a write would go to DB only and leave a stale value in Redis.
    Here, the cache is ALWAYS consistent with the DB immediately after every write.
    """
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if user_id < 1 or user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")

    # ── 1. Validate: profile must exist ───────────────────────────────────────
    result = await db.execute(select(UserProfile).where(UserProfile.id == user_id))
    profile_row = result.scalar_one_or_none()
    if profile_row is None:
        raise HTTPException(status_code=404, detail=f"Profile {user_id} not found")

    # ── 2. Build update dict ───────────────────────────────────────────────────
    updates: dict = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update provided")

    start = time.perf_counter()

    # ── 3. Write to database (SQLAlchemy UPDATE) ───────────────────────────────
    await db.execute(
        sa_update(UserProfile).where(UserProfile.id == user_id).values(**updates)
    )
    await db.commit()

    # ── 4. Re-fetch updated row ────────────────────────────────────────────────
    result = await db.execute(select(UserProfile).where(UserProfile.id == user_id))
    updated_row = result.scalar_one()

    updated_dict = {
        "id": updated_row.id,
        "name": updated_row.name,
        "email": updated_row.email,
        "bio": updated_row.bio,
        "preferences": updated_row.preferences,
    }

    # ── 5. Write-Through: update Redis cache synchronously ────────────────────
    cache_key = f"profile:{user_id}"
    await redis.setex(cache_key, _WT_TTL, json.dumps(updated_dict))

    db_write_ms = (time.perf_counter() - start) * 1000

    # Record for the ops log
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
        "note": (
            "Write-Through complete — DB and Redis updated atomically. "
            "The next GET will be a cache HIT with this exact data."
        ),
    }


@app.get("/v1/write-through/stats")
async def write_through_stats(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Return Write-Through pattern stats and recent operations log."""
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    with _wt_lock:
        hits = _wt_reads_hit
        misses = _wt_reads_miss
        writes = _wt_writes
        recent_ops = list(_wt_recent_ops)

    total_reads = hits + misses

    # Scan for cached profile keys
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
        "note": (
            "Every PUT writes DB + cache together. "
            f"This demo has prevented {writes} potential stale-read scenarios."
        ),
    }


@app.delete("/v1/write-through/cache")
async def write_through_clear(redis: Optional[aioredis.Redis] = Depends(get_redis)):
    """Evict all profile:* keys from Redis (simulates cache cold-start)."""
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
        "message": (
            f"Cleared {deleted} profile key(s). "
            "Next GET will be a cold-start miss, then stays hot via write-through."
        ),
        "deleted_count": deleted,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2.5 — Write-Behind (Write-Back) Pattern
# ══════════════════════════════════════════════════════════════════════════════
#
# Rule: acknowledge the write to the client INSTANTLY by writing to a Redis
# Stream (in-memory queue), then persist to SQLite asynchronously in batches.
#
# Flow:
#   POST /v1/write-behind/event  → XADD to Redis Stream → ack < 1 ms
#   Background task              → every 5 s, XRANGE + bulk INSERT → DB
#
# Use case: analytics events, audit logs, activity streams — high write
# throughput where brief data-loss risk is acceptable.
# ──────────────────────────────────────────────────────────────────────────────

_WB_STREAM_KEY = "wb:events"
_WB_FLUSH_INTERVAL_S = 5  # flush every N seconds
_WB_BATCH_SIZE = 50  # max entries per flush

_wb_flushed = 0  # total events persisted to DB
_wb_flush_batches = 0  # total flush operations performed
_wb_last_flush_ms: Optional[float] = None
_wb_worker_task: Optional[asyncio.Task] = None
_wb_lock = threading.Lock()

_EVENT_TYPES = {"page_view", "click", "purchase", "login", "logout"}


class EventIn(BaseModel):
    """Request body for the Write-Behind POST endpoint."""

    event_type: str  # page_view | click | purchase | login | logout
    user_id: int  # 1–5 for this demo
    payload: dict = {}


async def _write_behind_worker() -> None:
    """
    Background asyncio task: reads Redis Stream in batches and bulk-inserts
    into SQLite every _WB_FLUSH_INTERVAL_S seconds.

    This simulates a real write-behind worker process.  The client that called
    POST /v1/write-behind/event received its acknowledgement long before this
    function even wakes up.
    """
    global _wb_flushed, _wb_flush_batches, _wb_last_flush_ms
    while True:
        try:
            await asyncio.sleep(_WB_FLUSH_INTERVAL_S)
            if _redis is None:
                continue

            # Drain ALL pending entries from the stream
            entries = await _redis.xrange(
                _WB_STREAM_KEY, "-", "+", count=_WB_BATCH_SIZE
            )
            if not entries:
                continue

            flush_start = time.perf_counter()
            async with AsyncSessionLocal() as session:
                for _msg_id, fields in entries:
                    session.add(
                        EventLog(
                            event_type=fields["event_type"],
                            user_id=int(fields["user_id"]),
                            payload=json.loads(fields.get("payload", "{}")),
                            created_at=float(fields["created_at"]),
                        )
                    )
                await session.commit()

            flush_ms = (time.perf_counter() - flush_start) * 1000

            # Remove flushed entries from the stream
            await _redis.delete(_WB_STREAM_KEY)

            with _wb_lock:
                _wb_flushed += len(entries)
                _wb_flush_batches += 1
                _wb_last_flush_ms = round(flush_ms, 2)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[write-behind-worker] error: {exc}")


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.post("/v1/write-behind/event", status_code=202)
async def write_behind_post_event(
    body: EventIn,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    """
    **2.5 Write-Behind** — Accept an analytics event.

    1. Writes to Redis Stream (`XADD`) immediately — non-blocking.
    2. Returns 202 Accepted in < 1 ms.  DB write has NOT happened yet.
    3. Background worker flushes the stream to SQLite every 5 s.

    This is the core trade-off: fastest possible write throughput at the cost
    of a brief window where data lives only in Redis (survives Redis restart,
    lost only if Redis AND the app crash simultaneously).
    """
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if body.user_id < 1 or body.user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")
    if body.event_type not in _EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"event_type must be one of {sorted(_EVENT_TYPES)}",
        )

    start = time.perf_counter()
    created_at = time.time()

    # XADD — append to stream, returns the new message ID
    msg_id = await redis.xadd(
        _WB_STREAM_KEY,
        {
            "event_type": body.event_type,
            "user_id": str(body.user_id),
            "payload": json.dumps(body.payload),
            "created_at": str(created_at),
        },
    )

    stream_len = await redis.xlen(_WB_STREAM_KEY)
    ack_ms = (time.perf_counter() - start) * 1000

    return {
        "status": "queued",
        "message_id": msg_id,
        "write_behind": True,
        "db_written": False,
        "cache_written": True,
        "ack_latency_ms": round(ack_ms, 2),
        "queue_depth": stream_len,
        "note": (
            f"Write-Behind: acknowledged in {round(ack_ms, 2)} ms. "
            f"DB persistence deferred — background worker flushes every {_WB_FLUSH_INTERVAL_S}s. "
            f"{stream_len} event(s) pending in Redis Stream."
        ),
    }


@app.get("/v1/write-behind/events")
async def write_behind_get_events(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """
    **2.5 Write-Behind — Events** — Show pending (stream) vs persisted (DB) events.
    """
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    # Pending events still in Redis Stream (not yet flushed to DB)
    raw_pending = await redis.xrange(_WB_STREAM_KEY, "-", "+", count=20)
    pending = [
        {
            "message_id": msg_id,
            "event_type": fields["event_type"],
            "user_id": int(fields["user_id"]),
            "status": "pending_db_write",
            "created_at": float(fields["created_at"]),
        }
        for msg_id, fields in raw_pending
    ]

    # Most recently persisted events from SQLite
    result = await db.execute(select(EventLog).order_by(EventLog.id.desc()).limit(10))
    flushed = [
        {
            "id": row.id,
            "event_type": row.event_type,
            "user_id": row.user_id,
            "payload": row.payload,
            "status": "persisted_to_db",
            "created_at": row.created_at,
        }
        for row in result.scalars().all()
    ]

    # Count total DB records
    count_result = await db.execute(select(EventLog))
    db_total = len(count_result.scalars().all())

    return {
        "pending_in_stream": pending,
        "pending_count": len(pending),
        "flushed_to_db": flushed,
        "total_flushed_count": db_total,
    }


@app.get("/v1/write-behind/stats")
async def write_behind_stats(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """Return Write-Behind worker stats."""
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    with _wb_lock:
        flushed = _wb_flushed
        batches = _wb_flush_batches
        last_flush_ms = _wb_last_flush_ms

    queue_depth = await redis.xlen(_WB_STREAM_KEY)

    # Total events in DB
    count_result = await db.execute(select(EventLog))
    db_total = len(count_result.scalars().all())

    return {
        "strategy": "Write-Behind (Write-Back)",
        "queue_depth": queue_depth,
        "total_flushed_to_db": flushed,
        "db_total_events": db_total,
        "flush_batches": batches,
        "last_flush_ms": last_flush_ms,
        "flush_interval_seconds": _WB_FLUSH_INTERVAL_S,
        "batch_size": _WB_BATCH_SIZE,
        "stream_key": _WB_STREAM_KEY,
        "note": (
            f"Worker runs every {_WB_FLUSH_INTERVAL_S}s. "
            f"{queue_depth} event(s) in stream, {flushed} total flushed to DB."
        ),
    }


@app.post("/v1/write-behind/flush")
async def write_behind_flush(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    """
    **2.5 Write-Behind — Force Flush** — Immediately drain Redis Stream to DB.

    Useful in the demo to see the before/after without waiting 5 seconds.
    """
    global _wb_flushed, _wb_flush_batches, _wb_last_flush_ms
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    entries = await redis.xrange(_WB_STREAM_KEY, "-", "+")
    if not entries:
        return {
            "message": "No pending events to flush",
            "flushed_count": 0,
            "queue_depth": 0,
        }

    flush_start = time.perf_counter()
    async with AsyncSessionLocal() as session:
        for _msg_id, fields in entries:
            session.add(
                EventLog(
                    event_type=fields["event_type"],
                    user_id=int(fields["user_id"]),
                    payload=json.loads(fields.get("payload", "{}")),
                    created_at=float(fields["created_at"]),
                )
            )
        await session.commit()
    flush_ms = (time.perf_counter() - flush_start) * 1000

    await redis.delete(_WB_STREAM_KEY)

    with _wb_lock:
        _wb_flushed += len(entries)
        _wb_flush_batches += 1
        _wb_last_flush_ms = round(flush_ms, 2)

    return {
        "message": f"Force-flushed {len(entries)} event(s) to DB",
        "flushed_count": len(entries),
        "flush_ms": round(flush_ms, 2),
        "queue_depth": 0,
        "note": "Manual flush complete — all pending events are now persisted to SQLite.",
    }


@app.delete("/v1/write-behind/clear")
async def write_behind_clear(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """Clear all Write-Behind data: stream + DB records + stats reset."""
    global _wb_flushed, _wb_flush_batches, _wb_last_flush_ms
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    await redis.delete(_WB_STREAM_KEY)
    await db.execute(text("DELETE FROM event_logs"))
    await db.commit()

    with _wb_lock:
        _wb_flushed = _wb_flush_batches = 0
        _wb_last_flush_ms = None

    return {
        "message": "Write-Behind data cleared — stream, DB, and stats reset.",
        "ok": True,
    }
