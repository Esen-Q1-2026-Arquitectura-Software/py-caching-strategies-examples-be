"""
Caching Showcase — FastAPI Backend  (entry point)
===================================================
Strategies implemented:
  2.1  In-Memory:  functools.lru_cache + cachetools.TTLCache
  2.2  Redis:      SETEX/GET cache-aside
  2.3  Cache-Aside (Lazy Loading) with negative caching
  2.4  Write-Through
  2.5  Write-Behind (Write-Back) via Redis Stream + background worker
  2.6  Read-Through via aiocache @cached decorator
  2.7  Response Caching Middleware (BaseHTTPMiddleware)
  2.8  HTTP Caching with FastAPI (ETag, Cache-Control, 304 Not Modified)  2.9  Async Caching with aiocache (MsgPack, plugins, multi_get/multi_set)
  2.10 Database Query Caching (hash-keyed SQL result cache, SCAN-safe eviction)
To add a new strategy:
  1. Create be/routers/sNN_name.py with an APIRouter
  2. Import and include it below with app.include_router(...)
"""

import core.redis_client  # noqa: F401 — ensures _redis is importable from middleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from lifespan import lifespan
from middleware.response_cache import ResponseCacheMiddleware
from routers import (
    s21_lru,
    s21_ttl,
    s22_redis,
    s23_cache_aside,
    s24_write_through,
    s25_write_behind,
    s26_read_through,
    s27_middleware,
    s28_http_cache,
    s29_aiocache,
    s210_db_query_cache,
    s211_stampede,
)

app = FastAPI(
    title="Caching Showcase API",
    description="FastAPI backend demonstrating 2.1-2.11 caching strategies",
    version="2.11.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# ResponseCacheMiddleware must be registered AFTER CORSMiddleware.
# Starlette middleware stack is LIFO — last added = outermost (first to run).
app.add_middleware(ResponseCacheMiddleware)

# Routers
app.include_router(s21_lru.router)
app.include_router(s21_ttl.router)
app.include_router(s22_redis.router)
app.include_router(s23_cache_aside.router)
app.include_router(s24_write_through.router)
app.include_router(s25_write_behind.router)
app.include_router(s26_read_through.router)
app.include_router(s27_middleware.router)
app.include_router(s28_http_cache.router)
app.include_router(s29_aiocache.router)
app.include_router(s210_db_query_cache.router)
app.include_router(s211_stampede.router)


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
            "2.6 Read-Through (@cached decorator)",
            "2.7 Response Caching Middleware",
            "2.8 HTTP Caching (ETag, Cache-Control, 304 Not Modified)",
            "2.9 Async aiocache (MsgPack, plugins, multi_get/multi_set)",
            "2.10 DB Query Caching (hash-keyed SQL result cache)",
            "2.11 Cache Stampede Prevention (Mutex Lock)",
        ],
    }


@app.get("/health")
async def health():
    import core.redis_client as _redis_mod

    redis_ok = False
    if _redis_mod._redis:
        try:
            await _redis_mod._redis.ping()
            redis_ok = True
        except Exception:
            pass
    return {"status": "ok", "redis": redis_ok}
