"""
2.5 — Write-Behind (Write-Back) Pattern
Writes go to a Redis Stream instantly (< 1 ms ack), then a background worker
bulk-flushes them to SQLite every 5 seconds.
"""

import asyncio
import json
import threading
import time
from typing import Optional

import redis.asyncio as aioredis
from core.database import AsyncSessionLocal, get_db
from core.models import EventLog
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/v1/write-behind", tags=["2.5 Write-Behind"])

_WB_STREAM_KEY = "wb:events"
_WB_FLUSH_INTERVAL_S = 5  # flush every N seconds
_WB_BATCH_SIZE = 50  # max entries per flush

_wb_flushed = 0
_wb_flush_batches = 0
_wb_last_flush_ms: Optional[float] = None
_wb_worker_task: Optional[asyncio.Task] = None
_wb_lock = threading.Lock()

_EVENT_TYPES = {"page_view", "click", "purchase", "login", "logout"}


class EventIn(BaseModel):
    event_type: str
    user_id: int
    payload: dict = {}


async def _write_behind_worker() -> None:
    """Background task: drains the Redis Stream to SQLite every _WB_FLUSH_INTERVAL_S seconds."""
    global _wb_flushed, _wb_flush_batches, _wb_last_flush_ms

    import core.redis_client as _redis_mod  # local import to avoid circular dep

    while True:
        try:
            await asyncio.sleep(_WB_FLUSH_INTERVAL_S)
            redis = _redis_mod._redis
            if redis is None:
                continue

            entries = await redis.xrange(_WB_STREAM_KEY, "-", "+", count=_WB_BATCH_SIZE)
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
            await redis.delete(_WB_STREAM_KEY)

            with _wb_lock:
                _wb_flushed += len(entries)
                _wb_flush_batches += 1
                _wb_last_flush_ms = round(flush_ms, 2)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[write-behind-worker] error: {exc}")


@router.post("/event", status_code=202)
async def write_behind_post_event(
    body: EventIn,
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    if body.user_id < 1 or body.user_id > 5:
        raise HTTPException(status_code=400, detail="user_id must be 1–5 for this demo")
    if body.event_type not in _EVENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"event_type must be one of {sorted(_EVENT_TYPES)}"
        )

    start = time.perf_counter()
    created_at = time.time()

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


@router.get("/events")
async def write_behind_get_events(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

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

    count_result = await db.execute(select(EventLog))
    db_total = len(count_result.scalars().all())

    return {
        "pending_in_stream": pending,
        "pending_count": len(pending),
        "flushed_to_db": flushed,
        "total_flushed_count": db_total,
    }


@router.get("/stats")
async def write_behind_stats(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    with _wb_lock:
        flushed = _wb_flushed
        batches = _wb_flush_batches
        last_flush_ms = _wb_last_flush_ms

    queue_depth = await redis.xlen(_WB_STREAM_KEY)
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
        "note": f"Worker runs every {_WB_FLUSH_INTERVAL_S}s. {queue_depth} event(s) in stream, {flushed} total flushed to DB.",
    }


@router.post("/flush")
async def write_behind_flush(redis: Optional[aioredis.Redis] = Depends(get_redis)):
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


@router.delete("/clear")
async def write_behind_clear(
    redis: Optional[aioredis.Redis] = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
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
