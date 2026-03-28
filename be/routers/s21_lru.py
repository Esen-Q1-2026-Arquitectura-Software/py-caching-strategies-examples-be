"""
2.1 — functools.lru_cache
No TTL, LRU eviction, lives for the lifetime of the process.
"""

import asyncio
import time
from functools import lru_cache

from fastapi import APIRouter

router = APIRouter(prefix="/v1/lru", tags=["2.1 LRU Cache"])

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


@router.get("/country/{code}")
async def lru_get_country(code: str):
    code = code.upper()
    start = time.perf_counter()
    info_before = _lru_fetch_country.cache_info()
    data = await asyncio.to_thread(_lru_fetch_country, code)
    info_after = _lru_fetch_country.cache_info()
    cache_hit = info_after.hits > info_before.hits
    return {
        "data": data,
        "cache_hit": cache_hit,
        "source": "lru_cache" if cache_hit else "simulated_db",
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "ttl_remaining_seconds": None,
        "note": "lru_cache — no TTL, lives in process RAM, LRU eviction at maxsize=128",
    }


@router.get("/stats")
async def lru_stats():
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


@router.delete("/cache")
async def lru_clear():
    _lru_fetch_country.cache_clear()
    return {
        "message": "lru_cache cleared — next call will be a cache miss",
        "cache_size": 0,
    }
