"""
2.10 — Database Query Caching (SQLAlchemy)
==========================================
Demonstrates caching the results of arbitrary SQL queries to avoid repeated
heavy DB operations. Core pattern from the spec:

  key = "dq:" + md5(f"{query_name}:{json.dumps(params, sort_keys=True)}").hexdigest()

  cached = await redis.get(key)
  if cached:
      return json.loads(cached)          # HIT — no DB touch

  async with session:                    # MISS — execute SQL
      result = await conn.execute(text(sql), params)
      rows = [dict(r._mapping) for r in result.fetchall()]

  await redis.setex(key, ttl, json.dumps(rows))
  return rows

Key points:
  • Cache key encodes BOTH the query name AND its parameters — different filter
    values produce distinct keys so each result set is cached independently.
  • Clearing by namespace — scan for "dq:*" keys and delete all at once.
  • Shows the SQL text, bound parameters, and computed cache key in every
    response so the demo can make the pattern visible.
  • 80 ms artificial DB latency simulates a real expensive query (index scan,
    aggregation, JOIN) so the speedup is immediately observable in the UI.
"""

import asyncio
import hashlib
import json
import time
from typing import Any, Optional

import redis.asyncio as aioredis
from core.database import AsyncSessionLocal
from core.models import Employee
from core.redis_client import get_redis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text

router = APIRouter(prefix="/v1/db-query", tags=["2.10 DB Query Cache"])

_DQ_TTL = 120  # 2-minute TTL — short enough to see re-population in a demo
_NS = "dq"  # Redis key prefix for all cached queries in this strategy
_FAKE_LATENCY = 0.08  # 80 ms simulated "expensive query" latency

# ── Module-level counters (reset via POST /reset) ─────────────────────────────
_hits: int = 0
_misses: int = 0
_hits_by_query: dict[str, int] = {}
_misses_by_query: dict[str, int] = {}


# ── Generic cached_query helper ───────────────────────────────────────────────


def _make_cache_key(query_name: str, params: dict) -> str:
    """
    Deterministic Redis key from query name + bound parameters.

    Using json.dumps with sort_keys=True guarantees the same key regardless of
    dict insertion order — critical for correctness when parameters are assembled
    from request args in varying order.
    """
    raw = f"{query_name}:{json.dumps(params, sort_keys=True)}"
    digest = hashlib.md5(raw.encode()).hexdigest()
    return f"{_NS}:{digest}"


async def cached_query(
    query_name: str,
    sql: str,
    params: dict,
    redis: aioredis.Redis,
    ttl: int = _DQ_TTL,
) -> tuple[list[dict], bool, str]:
    """
    Execute *sql* with *params*, caching the result set in Redis under a key
    derived from *query_name* + *params*.

    Returns:
        (rows, cache_hit, cache_key)
    """
    global _hits, _misses

    key = _make_cache_key(query_name, params)
    cached = await redis.get(key)

    if cached:
        _hits += 1
        _hits_by_query[query_name] = _hits_by_query.get(query_name, 0) + 1
        return json.loads(cached), True, key

    # ── MISS: add artificial latency, run the real query ──────────────────────
    _misses += 1
    _misses_by_query[query_name] = _misses_by_query.get(query_name, 0) + 1

    await asyncio.sleep(_FAKE_LATENCY)
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        rows = [dict(r._mapping) for r in result.fetchall()]

    await redis.setex(key, ttl, json.dumps(rows, default=str))
    return rows, False, key


# ── SQL definitions — all queries run against the `employees` table ───────────

_SQL_EMPLOYEES_FILTER = """
SELECT id, name, department, role, salary, hire_year, active
FROM employees
WHERE (:department IS NULL OR department = :department)
  AND (:hire_year_min IS NULL OR hire_year >= :hire_year_min)
  AND (:active_only = 0 OR active = 1)
ORDER BY
  CASE WHEN :sort_col = 'salary_desc'    THEN salary    END DESC,
  CASE WHEN :sort_col = 'salary_asc'     THEN salary    END ASC,
  CASE WHEN :sort_col = 'hire_year_desc' THEN hire_year END DESC,
  CASE WHEN :sort_col = 'hire_year_asc'  THEN hire_year END ASC,
  id ASC
""".strip()

_SQL_DEPT_STATS = """
SELECT
  department,
  COUNT(*)      AS headcount,
  ROUND(AVG(salary), 2) AS avg_salary,
  MAX(salary)   AS max_salary,
  MIN(salary)   AS min_salary,
  SUM(salary)   AS total_payroll
FROM employees
WHERE active = 1
GROUP BY department
ORDER BY avg_salary DESC
""".strip()

_SQL_TOP_EARNERS = """
SELECT id, name, department, role, salary, hire_year
FROM employees
ORDER BY salary DESC
LIMIT :limit
""".strip()


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/employees")
async def list_employees(
    department: Optional[str] = Query(None, description="Filter by department name"),
    sort: str = Query(
        "salary_desc",
        description="salary_desc | salary_asc | hire_year_desc | hire_year_asc",
    ),
    hire_year_min: Optional[int] = Query(
        None, description="Only employees hired this year or later"
    ),
    active_only: bool = Query(False, description="Only include active employees"),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Filtered employee list cached by the exact combination of filter parameters.

    Two requests with the same filters return identical results from Redis on
    the second call (< 1 ms). Changing ANY parameter produces a distinct cache
    key and triggers a fresh DB query.

    The response includes the raw SQL, bound parameters, and the Redis key so
    the pattern is transparent.
    """
    params = {
        "department": department,
        "sort_col": sort,
        "hire_year_min": hire_year_min,
        "active_only": 1 if active_only else 0,
    }

    t0 = time.perf_counter()
    rows, hit, key = await cached_query(
        query_name="employees_filter",
        sql=_SQL_EMPLOYEES_FILTER,
        params=params,
        redis=redis,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "data": rows,
        "count": len(rows),
        "cache_hit": hit,
        "source": "redis" if hit else "sqlite_db",
        "latency_ms": latency_ms,
        "cache_key": key,
        "ttl_seconds": _DQ_TTL,
        "query_name": "employees_filter",
        "sql": _SQL_EMPLOYEES_FILTER,
        "params": params,
        "note": (
            "Cache key = md5('employees_filter:' + json(params, sort_keys=True)). "
            "Changing any filter = new key = fresh DB query."
        ),
    }


@router.get("/departments/stats")
async def department_stats(
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Department-level aggregation (headcount, avg/min/max salary, total payroll).

    This is the most expensive query in the demo: a full table scan with GROUP BY
    and five aggregate functions. First call takes ~80 ms; subsequent calls
    return from Redis in < 1 ms — a 80-100x speedup.

    No parameters → single stable cache key → extremely high hit rate after
    the first request.
    """
    params: dict = {}

    t0 = time.perf_counter()
    rows, hit, key = await cached_query(
        query_name="dept_stats",
        sql=_SQL_DEPT_STATS,
        params=params,
        redis=redis,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "data": rows,
        "cache_hit": hit,
        "source": "redis" if hit else "sqlite_db",
        "latency_ms": latency_ms,
        "cache_key": key,
        "ttl_seconds": _DQ_TTL,
        "query_name": "dept_stats",
        "sql": _SQL_DEPT_STATS,
        "params": params,
        "note": (
            "Zero parameters → single stable cache key. "
            "This query is cached once and hits on every subsequent call until TTL expires."
        ),
    }


@router.get("/top-earners")
async def top_earners(
    limit: int = Query(5, ge=1, le=20, description="Number of top earners to return"),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Top-N earners sorted by salary descending.

    The `limit` value is a query parameter — changing it generates a different
    cache key. This demonstrates that parametric queries are cached independently
    per distinct parameter value, not per query type.
    """
    params = {"limit": limit}

    t0 = time.perf_counter()
    rows, hit, key = await cached_query(
        query_name="top_earners",
        sql=_SQL_TOP_EARNERS,
        params=params,
        redis=redis,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "data": rows,
        "count": len(rows),
        "cache_hit": hit,
        "source": "redis" if hit else "sqlite_db",
        "latency_ms": latency_ms,
        "cache_key": key,
        "ttl_seconds": _DQ_TTL,
        "query_name": "top_earners",
        "sql": _SQL_TOP_EARNERS,
        "params": params,
        "note": (
            f"limit={limit} → unique cache key. "
            "Requesting limit=5 and limit=10 are cached under separate keys."
        ),
    }


@router.delete("/cache")
async def clear_cache(
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Remove all cached query results in the 'dq:' namespace.

    Uses SCAN (not KEYS) to iterate safely — KEYS blocks the Redis event loop
    and is forbidden in production. SCAN iterates in small batches.
    """
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        if keys:
            await redis.delete(*keys)
            deleted += len(keys)
        if cursor == 0:
            break

    return {
        "cleared": True,
        "keys_deleted": deleted,
        "namespace": _NS,
        "note": "Used SCAN (not KEYS) — safe for production Redis.",
    }


@router.get("/stats")
async def query_cache_stats(
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Hit/miss counters per query type + total, with a live count of cached keys
    currently in Redis and a cache key anatomy explainer.
    """
    # Count live cached keys
    live_keys = 0
    cursor = 0
    key_examples: list[str] = []
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{_NS}:*", count=100)
        live_keys += len(keys)
        for k in keys:
            if len(key_examples) < 5:
                key_examples.append(k.decode() if isinstance(k, bytes) else k)
        if cursor == 0:
            break

    total = _hits + _misses
    per_query = {}
    for q in set(list(_hits_by_query.keys()) + list(_misses_by_query.keys())):
        qh = _hits_by_query.get(q, 0)
        qm = _misses_by_query.get(q, 0)
        qt = qh + qm
        per_query[q] = {
            "hits": qh,
            "misses": qm,
            "total": qt,
            "hit_ratio_pct": round(qh / qt * 100, 1) if qt else 0,
        }

    return {
        "totals": {
            "hits": _hits,
            "misses": _misses,
            "total": total,
            "hit_ratio_pct": round(_hits / total * 100, 1) if total else 0,
        },
        "per_query": per_query,
        "redis": {
            "live_cached_keys": live_keys,
            "key_examples": key_examples,
            "namespace": _NS,
            "ttl_seconds": _DQ_TTL,
        },
        "cache_key_anatomy": {
            "format": f"{_NS}:<md5_hex>",
            "md5_input": "'{query_name}:{json.dumps(params, sort_keys=True)}'",
            "example": {
                "query_name": "employees_filter",
                "params": {
                    "department": "Engineering",
                    "sort_col": "salary_desc",
                    "hire_year_min": None,
                    "active_only": 0,
                },
                "raw_input": 'employees_filter:{"active_only": 0, "department": "Engineering", '
                '"hire_year_min": null, "sort_col": "salary_desc"}',
                "key": _make_cache_key(
                    "employees_filter",
                    {
                        "department": "Engineering",
                        "sort_col": "salary_desc",
                        "hire_year_min": None,
                        "active_only": 0,
                    },
                ),
            },
        },
    }


@router.post("/reset")
async def reset_stats():
    """Reset all hit/miss counters (does NOT clear Redis cache)."""
    global _hits, _misses, _hits_by_query, _misses_by_query
    _hits = _misses = 0
    _hits_by_query = {}
    _misses_by_query = {}
    return {
        "reset": True,
        "message": "Counters cleared. Redis cache keys are unchanged.",
    }
