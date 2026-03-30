"""
2.12 — Thundering Herd Problem Solutions
=========================================
When many independent cache keys expire at the same moment, every concurrent
request finds a MISS and simultaneously floods the database — the "thundering
herd" (batch stampede across multiple keys).

Two complementary defences demonstrated here:

  A) TTL JITTER
     Each key is given a TTL of BASE_TTL ± 25 % at random, so the entire
     cohort of keys expires spread across a window instead of all at once.

  B) CIRCUIT BREAKER
     Consecutive DB failures are counted.  After THRESHOLD failures the
     breaker "opens" and short-circuits all requests, shielding the DB.
     After RECOVERY_S seconds it enters "half-open" and allows one probe.
     On success it resets to "closed".

Namespace:  th212
"""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from enum import Enum

import redis.asyncio as aioredis
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/v1/herd", tags=["2.12 Thundering Herd"])

_NS = "th212"
_COMPUTE_DELAY = 0.10  # 100 ms simulated DB latency
_BASE_TTL = 20  # seconds
_JITTER_PCT = 0.25  # ± 25 %
_ITEMS = 10  # keys in the jitter demo

# ── Module-level stats ─────────────────────────────────────────────────────────
_stats: dict = {
    "plain_hits": 0,
    "plain_misses": 0,
    "plain_db_calls": 0,
    "jitter_hits": 0,
    "jitter_misses": 0,
    "jitter_db_calls": 0,
    "cb_allowed": 0,
    "cb_blocked": 0,
    "cb_failures_count": 0,
    "cb_recoveries": 0,
}

# ── Jitter helpers ─────────────────────────────────────────────────────────────


def _jitter_ttl() -> int:
    window = int(_BASE_TTL * _JITTER_PCT)
    return _BASE_TTL + random.randint(-window, window)


def _article(article_id: int) -> dict:
    titles = [
        "FastAPI Performance Deep-Dive",
        "Redis Data Structures Explained",
        "Python asyncio Patterns",
        "Cache Invalidation Strategies",
        "Distributed Systems Primer",
        "Event-Driven Architecture",
        "gRPC vs REST Comparison",
        "PostgreSQL Query Tuning",
        "Kubernetes Networking 101",
        "Service Mesh in Practice",
    ]
    return {
        "id": article_id,
        "title": titles[(article_id - 1) % len(titles)],
        "views": random.randint(1_000, 50_000),
        "computed_at": round(time.time(), 3),
    }


# ── Circuit Breaker ────────────────────────────────────────────────────────────


class _CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _CircuitBreaker:
    threshold: int = 3
    recovery_s: float = 15.0
    state: _CBState = _CBState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    last_error: str = ""

    def allow_request(self) -> bool:
        if self.state == _CBState.CLOSED:
            return True
        if self.state == _CBState.OPEN:
            if time.time() - self.opened_at >= self.recovery_s:
                self.state = _CBState.HALF_OPEN
                return True  # one probe allowed
            return False
        return True  # HALF_OPEN: allow through

    def on_success(self) -> None:
        if self.state != _CBState.CLOSED:
            _stats["cb_recoveries"] += 1
        self.failures = 0
        self.state = _CBState.CLOSED

    def on_failure(self, err: str = "") -> None:
        self.failures += 1
        self.last_error = err
        _stats["cb_failures_count"] += 1
        if self.failures >= self.threshold:
            self.state = _CBState.OPEN
            self.opened_at = time.time()

    def as_dict(self) -> dict:
        elapsed = time.time() - self.opened_at if self.state != _CBState.CLOSED else 0.0
        return {
            "state": self.state.value,
            "failures": self.failures,
            "threshold": self.threshold,
            "recovery_s": self.recovery_s,
            "elapsed_open_s": round(elapsed, 1),
            "remaining_s": round(max(0.0, self.recovery_s - elapsed), 1),
            "last_error": self.last_error,
        }


_cb = _CircuitBreaker()
_inject_failure = False  # toggled by POST /cb/inject-failure


# ── Jitter endpoints ───────────────────────────────────────────────────────────


@router.get("/article/{article_id}/plain")
async def get_article_plain(
    article_id: int, redis: aioredis.Redis = Depends(get_redis)
):
    """Fetch one article with a FIXED TTL (no jitter)."""
    key = f"{_NS}:article:{article_id}:plain"
    t0 = time.perf_counter()
    raw = await redis.get(key)
    if raw:
        _stats["plain_hits"] += 1
        data = json.loads(raw)
        data.update(
            source="cache",
            ttl_s=await redis.ttl(key),
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return data
    _stats["plain_misses"] += 1
    _stats["plain_db_calls"] += 1
    await asyncio.sleep(_COMPUTE_DELAY)
    result = _article(article_id)
    ttl = _BASE_TTL
    await redis.setex(key, ttl, json.dumps(result))
    result.update(
        source="db", ttl_s=ttl, latency_ms=round((time.perf_counter() - t0) * 1000, 1)
    )
    return result


@router.get("/article/{article_id}/jitter")
async def get_article_jitter(
    article_id: int, redis: aioredis.Redis = Depends(get_redis)
):
    """Fetch one article with a JITTERED TTL (±25%)."""
    key = f"{_NS}:article:{article_id}:jitter"
    t0 = time.perf_counter()
    raw = await redis.get(key)
    if raw:
        _stats["jitter_hits"] += 1
        data = json.loads(raw)
        data.update(
            source="cache",
            ttl_s=await redis.ttl(key),
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return data
    _stats["jitter_misses"] += 1
    _stats["jitter_db_calls"] += 1
    await asyncio.sleep(_COMPUTE_DELAY)
    result = _article(article_id)
    ttl = _jitter_ttl()
    await redis.setex(key, ttl, json.dumps(result))
    result.update(
        source="db", ttl_s=ttl, latency_ms=round((time.perf_counter() - t0) * 1000, 1)
    )
    return result


@router.get("/ttls")
async def get_ttls(redis: aioredis.Redis = Depends(get_redis)):
    """Return current TTL for all plain and jitter article keys."""
    plain, jitter = [], []
    for i in range(1, _ITEMS + 1):
        pt = await redis.ttl(f"{_NS}:article:{i}:plain")
        jt = await redis.ttl(f"{_NS}:article:{i}:jitter")
        plain.append({"id": i, "ttl_s": pt})
        jitter.append({"id": i, "ttl_s": jt})
    return {
        "plain": plain,
        "jitter": jitter,
        "base_ttl": _BASE_TTL,
        "jitter_pct": int(_JITTER_PCT * 100),
    }


@router.post("/populate")
async def populate(redis: aioredis.Redis = Depends(get_redis)):
    """Populate all 10 article keys — plain keys all get TTL=20s, jitter keys each get ±25% spread."""
    plain_ttls, jitter_ttls = [], []
    for i in range(1, _ITEMS + 1):
        payload = _article(i)
        pt = _BASE_TTL
        jt = _jitter_ttl()
        await redis.setex(f"{_NS}:article:{i}:plain", pt, json.dumps(payload))
        await redis.setex(f"{_NS}:article:{i}:jitter", jt, json.dumps(payload))
        plain_ttls.append(pt)
        jitter_ttls.append(jt)
    return {
        "seeded": _ITEMS,
        "plain_ttls": plain_ttls,
        "jitter_ttls": jitter_ttls,
        "ttl_spread_s": max(jitter_ttls) - min(jitter_ttls),
        "note": "Plain: all expire simultaneously. Jitter: spread over a window.",
    }


@router.post("/expire")
async def force_expire(redis: aioredis.Redis = Depends(get_redis)):
    """Delete all article cache keys — simulates a mass expiry event (thundering herd setup)."""
    deleted = 0
    for i in range(1, _ITEMS + 1):
        deleted += await redis.delete(f"{_NS}:article:{i}:plain")
        deleted += await redis.delete(f"{_NS}:article:{i}:jitter")
    return {
        "deleted_keys": deleted,
        "message": "All keys expired. Next burst = MISS storm.",
    }


@router.post("/simulate")
async def simulate_burst(
    count: int = Query(10, ge=1, le=20),
    mode: str = Query("plain"),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Fire `count` concurrent requests after clearing the simulation cache.
    mode=plain  → all keys get TTL=20s (all expire together next cycle)
    mode=jitter → each key gets TTL 15-25s (staggered expiry next cycle)
    Both produce N DB calls in the burst itself; the difference is WHEN
    keys expire on the next cycle — visible in the TTL spread returned.
    """
    if mode not in ("plain", "jitter"):
        mode = "plain"

    def sim_key(i: int) -> str:
        return f"{_NS}:sim:{mode}:{i}"

    # Clear all simulation keys so we get all misses (thundering herd scenario)
    for i in range(1, count + 1):
        await redis.delete(sim_key(i))

    db_calls = 0
    db_timestamps: list[float] = []
    burst_start = time.perf_counter()
    ts_lock = asyncio.Lock()

    async def one_request(req_id: int) -> dict:
        nonlocal db_calls
        key = sim_key(req_id)
        raw = await redis.get(key)
        if raw:
            return {"id": req_id, "source": "cache"}
        ts_ms = round((time.perf_counter() - burst_start) * 1000, 1)
        async with ts_lock:
            db_calls += 1
            db_timestamps.append(ts_ms)
        await asyncio.sleep(_COMPUTE_DELAY)
        ttl = _BASE_TTL if mode == "plain" else _jitter_ttl()
        await redis.setex(key, ttl, json.dumps(_article(req_id)))
        return {"id": req_id, "source": "db"}

    results = await asyncio.gather(*[one_request(i) for i in range(1, count + 1)])
    total_ms = round((time.perf_counter() - burst_start) * 1000, 1)

    from_db = sum(1 for r in results if r["source"] == "db")
    from_cache = sum(1 for r in results if r["source"] == "cache")
    new_ttls = [await redis.ttl(sim_key(i)) for i in range(1, count + 1)]
    ttl_min = min(new_ttls) if new_ttls else 0
    ttl_max = max(new_ttls) if new_ttls else 0

    return {
        "mode": mode,
        "count": count,
        "db_calls": from_db,
        "cache_hits": from_cache,
        "total_ms": total_ms,
        "db_call_timestamps_ms": db_timestamps,
        "new_ttls": new_ttls,
        "ttl_min_s": ttl_min,
        "ttl_max_s": ttl_max,
        "ttl_spread_s": ttl_max - ttl_min,
        "note": (
            f"All {from_db} requests hit DB. "
            f"Every key has TTL={_BASE_TTL}s -- they expire simultaneously next cycle."
            if mode == "plain"
            else f"All {from_db} requests hit DB. "
            f"Keys have TTLs {ttl_min}-{ttl_max}s (spread {ttl_max - ttl_min}s). "
            f"Next expiry is staggered, so DB load peaks are smaller."
        ),
    }


# ── Circuit Breaker endpoints ──────────────────────────────────────────────────


@router.get("/cb/status")
async def cb_status():
    """Current circuit breaker state."""
    return {
        "circuit_breaker": _cb.as_dict(),
        "db_failure_injected": _inject_failure,
    }


@router.get("/cb/fetch")
async def cb_fetch(redis: aioredis.Redis = Depends(get_redis)):
    """Fetch resource with circuit breaker protection. Toggle failure with POST /cb/inject-failure."""
    key = f"{_NS}:cb:resource"
    t0 = time.perf_counter()

    # Fast path: cache hit
    raw = await redis.get(key)
    if raw:
        return {
            "source": "cache",
            "data": json.loads(raw),
            "circuit_breaker": _cb.as_dict(),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    # Circuit breaker gate
    if not _cb.allow_request():
        _stats["cb_blocked"] += 1
        return {
            "source": "circuit_open",
            "data": None,
            "circuit_breaker": _cb.as_dict(),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "message": "Circuit OPEN — DB protected. Return default/stale data here.",
        }

    # DB attempt
    try:
        if _inject_failure:
            raise RuntimeError("Simulated DB connection timeout")
        await asyncio.sleep(_COMPUTE_DELAY)
        data = {
            "items": [f"resource_{i}" for i in range(1, 6)],
            "generated_at": round(time.time(), 3),
        }
        _cb.on_success()
        _stats["cb_allowed"] += 1
        await redis.setex(key, _BASE_TTL, json.dumps(data))
        return {
            "source": "db",
            "data": data,
            "circuit_breaker": _cb.as_dict(),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:
        _cb.on_failure(str(exc))
        _stats["cb_allowed"] += 1  # request got through, just failed
        return {
            "source": "db_error",
            "data": None,
            "circuit_breaker": _cb.as_dict(),
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


@router.post("/cb/inject-failure")
async def inject_failure():
    """Start simulating DB failures. Call /cb/fetch 3+ times to trip the breaker."""
    global _inject_failure
    _inject_failure = True
    return {
        "db_failure_injected": True,
        "message": f"DB failures ON. Call /cb/fetch {_cb.threshold} more times to trip the breaker.",
    }


@router.post("/cb/clear-failure")
async def clear_failure():
    """Stop simulating DB failures (breaker stays in current state)."""
    global _inject_failure
    _inject_failure = False
    return {"db_failure_injected": False, "message": "DB failures cleared."}


@router.post("/cb/reset")
async def reset_cb(redis: aioredis.Redis = Depends(get_redis)):
    """Reset circuit breaker to CLOSED state, clear failure injection and CB cache key."""
    global _inject_failure
    _inject_failure = False
    _cb.failures = 0
    _cb.state = _CBState.CLOSED
    _cb.last_error = ""
    await redis.delete(f"{_NS}:cb:resource")
    return {"message": "Circuit breaker reset to CLOSED. Cache cleared."}


# ── Shared ─────────────────────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats():
    return {
        "jitter": {
            "plain_hits": _stats["plain_hits"],
            "plain_misses": _stats["plain_misses"],
            "plain_db_calls": _stats["plain_db_calls"],
            "jitter_hits": _stats["jitter_hits"],
            "jitter_misses": _stats["jitter_misses"],
            "jitter_db_calls": _stats["jitter_db_calls"],
        },
        "circuit_breaker": {
            "allowed": _stats["cb_allowed"],
            "blocked": _stats["cb_blocked"],
            "failures": _stats["cb_failures_count"],
            "recoveries": _stats["cb_recoveries"],
            "state": _cb.as_dict(),
        },
    }


@router.delete("/cache")
async def clear_cache(redis: aioredis.Redis = Depends(get_redis)):
    """Delete all th212:* Redis keys."""
    cursor = 0
    deleted = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        if keys:
            deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return {"deleted_keys": deleted}


@router.post("/reset")
async def reset_all(redis: aioredis.Redis = Depends(get_redis)):
    """Reset stats, circuit breaker, failure injection, and all cache keys."""
    global _inject_failure
    _inject_failure = False
    _cb.failures = 0
    _cb.state = _CBState.CLOSED
    _cb.last_error = ""
    for k in _stats:
        _stats[k] = 0
    cursor = 0
    deleted = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        if keys:
            deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return {"message": "Reset complete.", "deleted_keys": deleted}
