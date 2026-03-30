import asyncio
import os
from contextlib import asynccontextmanager

import core.redis_client as _redis_mod
import redis.asyncio as aioredis
import routers.s25_write_behind as _s25
import routers.s216_event_driven as _s216
from core.database import engine
from core.models import Base
from core.seed import seed_database
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Create all SQLAlchemy tables ──────────────────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── Seed tables with demo data (skips if already populated) ───────────────
    await seed_database()

    # ── Connect Redis ─────────────────────────────────────────────────────────
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        _redis_mod._redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=2,
        )
        await _redis_mod._redis.ping()
        print(f"Redis connected: {redis_url}")
    except Exception as exc:
        print(f"WARNING: Redis unavailable ({exc}). Redis endpoints will return 503.")
        _redis_mod._redis = None

    # ── Start Write-Behind background worker ──────────────────────────────────
    if _redis_mod._redis:
        _s25._wb_worker_task = asyncio.create_task(_s25._write_behind_worker())
        print("Write-Behind background worker started.")

    # ── Start 2.16 Event-Driven Cache Invalidation Pub/Sub listener ───────────
    if _redis_mod._redis:
        _s216._listener_task = asyncio.create_task(_s216._invalidation_listener())
        print("[2.16] Pub/Sub invalidation listener started.")

    yield  # ── App running ───────────────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _s25._wb_worker_task is not None:
        _s25._wb_worker_task.cancel()
        try:
            await _s25._wb_worker_task
        except asyncio.CancelledError:
            pass

    if _s216._listener_task is not None:
        _s216._listener_task.cancel()
        try:
            await _s216._listener_task
        except asyncio.CancelledError:
            pass

    if _redis_mod._redis:
        await _redis_mod._redis.aclose()

    await engine.dispose()
