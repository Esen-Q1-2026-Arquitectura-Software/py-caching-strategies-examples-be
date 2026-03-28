"""
2.8 — HTTP Caching with FastAPI (ETag, Cache-Control)

Demonstrates native HTTP caching headers — no Redis, no application-layer
cache.  The browser (or any HTTP-compliant client) becomes the cache.

Techniques shown:
  ETag + If-None-Match          → 304 Not Modified (saves body bytes)
  Last-Modified + If-Modified-Since → 304 Not Modified (timestamp-based)
  Cache-Control directives       → public/private, max-age, stale-while-revalidate
  immutable                      → browser never revalidates within max-age window
  List-level ETag                → invalidates the entire collection on any change

No Redis dependency here — this is pure HTTP semantics handled by FastAPI.
"""

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import Optional

from core.database import get_db
from core.models import NewsArticle
from core.seed import _SEED_ARTICLES
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
    prefix="/v1/http-cache",
    tags=["2.8 HTTP Caching (ETag / Cache-Control)"],
)

# ── Cache-Control header values used across endpoints ─────────────────────────
_CC_ARTICLE = "public, max-age=60, stale-while-revalidate=30"
_CC_LIST = "public, max-age=30, stale-while-revalidate=15"
_CC_IMMUTABLE = "public, max-age=86400, immutable"

# ── Observability counters ────────────────────────────────────────────────────
_stats_lock = threading.Lock()
_etag_304s: int = 0  # 304s issued via If-None-Match
_lm_304s: int = 0  # 304s issued via If-Modified-Since
_full_200s: int = 0  # normal 200 responses (no match or first request)


def _inc_304_etag() -> None:
    global _etag_304s
    with _stats_lock:
        _etag_304s += 1


def _inc_304_lm() -> None:
    global _lm_304s
    with _stats_lock:
        _lm_304s += 1


def _inc_200() -> None:
    global _full_200s
    with _stats_lock:
        _full_200s += 1


# ── Helpers ───────────────────────────────────────────────────────────────────


def _compute_etag(data: dict) -> str:
    """
    Deterministic ETag: SHA-256 (first 16 hex chars) of sorted JSON.
    Changing ANY field in `data` produces a different ETag.
    """
    serialised = json.dumps(data, sort_keys=True, default=str)
    return f'"{hashlib.sha256(serialised.encode()).hexdigest()[:16]}"'


def _fmt_last_modified(ts: float) -> str:
    """RFC 7231 date string suitable for the Last-Modified HTTP header."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return format_datetime(dt, usegmt=True)


def _article_dict(a: NewsArticle) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "summary": a.summary,
        "body": a.body,
        "author": a.author,
        "category": a.category,
        "version": a.version,
        "updated_at": a.updated_at,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/article/{article_id}")
async def http_cache_get_article(
    article_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a single article WITH ETag, Last-Modified and Cache-Control headers.

    Conditional GET:
      • If-None-Match matches current ETag   → 304, no body  (ETag-based)
      • If-Modified-Since >= updated_at       → 304, no body  (timestamp-based)
      • Otherwise                             → 200, full JSON body
    """
    if article_id < 1 or article_id > 5:
        raise HTTPException(status_code=400, detail="article_id must be 1–5")

    article = await db.get(NewsArticle, article_id)
    if not article:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    data = _article_dict(article)
    etag = _compute_etag(data)
    last_modified_str = _fmt_last_modified(article.updated_at)

    response_headers = {
        "ETag": etag,
        "Last-Modified": last_modified_str,
        "Cache-Control": _CC_ARTICLE,
    }

    # ── Conditional GET: ETag takes precedence over Last-Modified ─────────────
    if_none_match = request.headers.get("if-none-match")
    if if_none_match:
        if if_none_match == etag:
            _inc_304_etag()
            return Response(status_code=304, headers=response_headers)
        # ETag present but doesn't match → fall through to 200

    # ── Conditional GET: Last-Modified (only when no If-None-Match) ───────────
    elif request.headers.get("if-modified-since"):
        try:
            client_dt = parsedate_to_datetime(request.headers["if-modified-since"])
            server_dt = datetime.fromtimestamp(article.updated_at, tz=timezone.utc)
            if server_dt <= client_dt:
                _inc_304_lm()
                return Response(status_code=304, headers=response_headers)
        except Exception:
            pass  # malformed date — serve 200 with full body

    # ── Full 200 response ─────────────────────────────────────────────────────
    _inc_200()
    data["_meta"] = {
        "etag": etag,
        "last_modified": last_modified_str,
        "cache_control": _CC_ARTICLE,
        "tip": (
            "Store this ETag, then repeat the request with "
            "'If-None-Match: <etag>' to receive a 304 — no body transferred."
        ),
    }
    return JSONResponse(content=data, headers=response_headers)


class _ArticleUpdate(BaseModel):
    body: str


@router.put("/article/{article_id}")
async def http_cache_update_article(
    article_id: int,
    payload: _ArticleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update an article's body.  Increments version + updated_at, which changes
    the ETag.  Any cached client holding the old ETag will receive a 200 on
    the next conditional GET instead of 304.
    """
    if article_id < 1 or article_id > 5:
        raise HTTPException(status_code=400, detail="article_id must be 1–5")
    if not payload.body or not payload.body.strip():
        raise HTTPException(status_code=422, detail="body must not be empty")

    article = await db.get(NewsArticle, article_id)
    if not article:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    article.body = payload.body.strip()
    article.version += 1
    article.updated_at = time.time()
    await db.commit()
    await db.refresh(article)

    data = _article_dict(article)
    new_etag = _compute_etag(data)
    last_modified_str = _fmt_last_modified(article.updated_at)

    data["_meta"] = {
        "etag": new_etag,
        "last_modified": last_modified_str,
        "tip": (
            "ETag changed. Any client holding the previous ETag will receive "
            "a full 200 response — the old ETag no longer matches."
        ),
    }
    return JSONResponse(
        content=data,
        headers={
            "ETag": new_etag,
            "Last-Modified": last_modified_str,
            # Clients must revalidate immediately after a mutation
            "Cache-Control": "no-cache",
        },
    )


@router.get("/articles")
async def http_cache_list_articles(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all articles.  The list ETag is computed from every article's
    (id, version) — it changes whenever ANY article is updated.
    """
    result = await db.execute(select(NewsArticle).order_by(NewsArticle.id))
    articles = result.scalars().all()

    # ETag for a collection = hash of sorted (id, version) tuples
    fingerprint = json.dumps([(a.id, a.version) for a in articles], sort_keys=True)
    list_etag = f'"{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}"'

    response_headers = {
        "ETag": list_etag,
        "Cache-Control": _CC_LIST,
    }

    if_none_match = request.headers.get("if-none-match")
    if if_none_match == list_etag:
        _inc_304_etag()
        return Response(status_code=304, headers=response_headers)

    _inc_200()
    data = {
        "articles": [_article_dict(a) for a in articles],
        "count": len(articles),
        "_meta": {
            "list_etag": list_etag,
            "cache_control": _CC_LIST,
            "tip": (
                "This ETag covers the ENTIRE list. "
                "Updating any single article changes this ETag, "
                "forcing a full re-fetch for all clients."
            ),
        },
    }
    return JSONResponse(content=data, headers=response_headers)


@router.get("/constants")
async def http_cache_constants():
    """
    An immutable resource.  Cache-Control: public, max-age=86400, immutable
    tells browsers to never revalidate within the max-age window — not even
    on hard refresh.  Use this pattern for content-addressed (hashed) URLs.
    """
    data = {
        "strategy": "2.8",
        "version": "1.0.0",
        "techniques": [
            "ETag + If-None-Match → 304 Not Modified",
            "Last-Modified + If-Modified-Since → 304 Not Modified",
            "Cache-Control: public, max-age=N",
            "Cache-Control: public, max-age=N, stale-while-revalidate=M",
            "Cache-Control: public, max-age=86400, immutable",
            "Cache-Control: no-cache (always revalidate)",
            "Cache-Control: no-store (never cache — sensitive data)",
        ],
        "cache_control": _CC_IMMUTABLE,
        "_meta": {
            "tip": (
                "This endpoint sets 'immutable' in Cache-Control. "
                "A browser that has already cached this response will serve it "
                "from its local cache for 86 400 s (24 h) without ever contacting "
                "the server again — zero network request, not even a HEAD check."
            ),
        },
    }
    return JSONResponse(content=data, headers={"Cache-Control": _CC_IMMUTABLE})


@router.get("/stats")
async def http_cache_stats():
    """Live counters: how many requests were answered with 304 vs 200."""
    total_304 = _etag_304s + _lm_304s
    total = _full_200s + total_304
    ratio = round(total_304 / total * 100, 1) if total > 0 else 0.0
    return {
        "etag_304s": _etag_304s,
        "last_modified_304s": _lm_304s,
        "total_304s": total_304,
        "full_200s": _full_200s,
        "total_requests": total,
        "not_modified_ratio_pct": ratio,
        "note": "304 responses carry zero body bytes — pure bandwidth savings.",
    }


@router.post("/reset")
async def http_cache_reset_articles(db: AsyncSession = Depends(get_db)):
    """
    Reset all articles to seed data.  Useful for demo reproducibility —
    after this, every ETag matches the seed-time hash again.
    """
    seed_map = {s["id"]: s for s in _SEED_ARTICLES}
    result = await db.execute(select(NewsArticle))
    articles = result.scalars().all()
    for article in articles:
        seed = seed_map.get(article.id)
        if seed:
            article.title = seed["title"]
            article.summary = seed["summary"]
            article.body = seed["body"]
            article.author = seed["author"]
            article.category = seed["category"]
            article.version = seed["version"]
            article.updated_at = seed["updated_at"]
    await db.commit()
    return {
        "status": "reset",
        "count": len(articles),
        "note": "All articles restored to seed values. Cached ETags are now valid again.",
    }
