"""
Caching Showcase — Flask Frontend
====================================
Thin proxy layer that forwards requests to the FastAPI backend and
serves the interactive showcase UI.

Pages:
  GET  /                        — landing page with strategy cards
  GET  /strategy/21             — 2.1 In-Memory Caching (lru_cache + TTLCache)
  GET  /strategy/22             — 2.2 Redis Cache
  GET  /strategy/23             — 2.3 Cache-Aside Pattern
  GET  /strategy/24             — 2.4 Write-Through Pattern
  GET  /strategy/25             — 2.5 Write-Behind (Write-Back) Pattern
  GET  /strategy/26             — 2.6 Read-Through Pattern (aiocache)
  GET  /strategy/27             — 2.7 FastAPI Response Caching Middleware

API Proxies: (see routes below)
"""

import os

import httpx
from flask import Flask, jsonify, make_response, render_template, request

app = Flask(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
_CLIENT_TIMEOUT = 10.0


def _proxy(method: str, path: str):
    """Forward a request to the backend and return a Flask JSON response."""
    url = f"{BACKEND_URL}{path}"
    try:
        kwargs = {}
        if method in ("POST", "PUT", "PATCH") and request.is_json:
            kwargs["json"] = request.get_json(silent=True) or {}
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.request(method, url, **kwargs)
        return jsonify(resp.json()), resp.status_code
    except httpx.RequestError as exc:
        return jsonify({"error": str(exc), "detail": "Backend unreachable"}), 502


def _proxy_xcache(method: str, path: str):
    """Like _proxy but also forwards X-Cache / X-Cache-Key response headers.

    Used exclusively by the 2.7 middleware demo routes so the browser JS can
    read r.headers.get('X-Cache') and show HIT / MISS directly.
    """
    url = f"{BACKEND_URL}{path}"
    try:
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.request(method, url)
        flask_resp = jsonify(resp.json())
        flask_resp.status_code = resp.status_code
        for header in ("X-Cache", "X-Cache-Key"):
            val = resp.headers.get(header)
            if val:
                flask_resp.headers[header] = val
        return flask_resp
    except httpx.RequestError as exc:
        return jsonify({"error": str(exc), "detail": "Backend unreachable"}), 502


def _proxy_conditional(method: str, path: str):
    """Forward conditional-GET headers (If-None-Match, If-Modified-Since) to
    the backend and propagate caching response headers (ETag, Last-Modified,
    Cache-Control) back to the browser.

    Used exclusively by the 2.8 HTTP Caching demo routes.
    A 304 response is returned as-is with no body so the browser JS can
    read resp.status and observe the 304 directly.
    """
    url = f"{BACKEND_URL}{path}"
    fwd_headers: dict = {}
    for h in ("If-None-Match", "If-Modified-Since"):
        val = request.headers.get(h)
        if val:
            fwd_headers[h] = val
    try:
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.request(method, url, headers=fwd_headers)
        if resp.status_code == 304:
            flask_resp = make_response("", 304)
        else:
            flask_resp = jsonify(resp.json())
            flask_resp.status_code = resp.status_code
        for h in ("ETag", "Last-Modified", "Cache-Control"):
            val = resp.headers.get(h)
            if val:
                flask_resp.headers[h] = val
        return flask_resp
    except httpx.RequestError as exc:
        return jsonify({"error": str(exc), "detail": "Backend unreachable"}), 502


# ── Pages ─────────────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/strategy/21")
def strategy_21():
    return render_template("strategy_21.html")


@app.get("/strategy/22")
def strategy_22():
    return render_template("strategy_22.html")


@app.get("/strategy/23")
def strategy_23():
    return render_template("strategy_23.html")


@app.get("/strategy/24")
def strategy_24():
    return render_template("strategy_24.html")


@app.get("/strategy/25")
def strategy_25():
    return render_template("strategy_25.html")


@app.get("/strategy/26")
def strategy_26():
    return render_template("strategy_26.html")


@app.get("/strategy/27")
def strategy_27():
    return render_template("strategy_27.html")


@app.get("/strategy/28")
def strategy_28():
    return render_template("strategy_28.html")


@app.get("/strategy/29")
def strategy_29():
    return render_template("strategy_29.html")


# ── lru_cache proxy ───────────────────────────────────────────────────────────


@app.get("/api/lru/country/<code>")
def lru_country(code: str):
    return _proxy("GET", f"/v1/lru/country/{code}")


@app.get("/api/lru/stats")
def lru_stats():
    return _proxy("GET", "/v1/lru/stats")


@app.route("/api/lru/cache", methods=["DELETE"])
def lru_clear():
    return _proxy("DELETE", "/v1/lru/cache")


# ── TTLCache proxy ────────────────────────────────────────────────────────────


@app.get("/api/ttl/user/<int:user_id>")
def ttl_user(user_id: int):
    return _proxy("GET", f"/v1/ttl/user/{user_id}")


@app.get("/api/ttl/stats")
def ttl_stats():
    return _proxy("GET", "/v1/ttl/stats")


@app.route("/api/ttl/cache", methods=["DELETE"])
def ttl_clear():
    return _proxy("DELETE", "/v1/ttl/cache")


# ── Redis proxy ───────────────────────────────────────────────────────────────


@app.get("/api/redis/product/<int:product_id>")
def redis_product(product_id: int):
    return _proxy("GET", f"/v1/redis/product/{product_id}")


@app.get("/api/redis/stats")
def redis_stats():
    return _proxy("GET", "/v1/redis/stats")


@app.route("/api/redis/cache", methods=["DELETE"])
def redis_clear():
    return _proxy("DELETE", "/v1/redis/cache")


# ── Cache-Aside (2.3) proxy ───────────────────────────────────────────────────


@app.get("/api/cache-aside/order/<int:order_id>")
def cache_aside_order(order_id: int):
    return _proxy("GET", f"/v1/cache-aside/order/{order_id}")


@app.get("/api/cache-aside/stats")
def cache_aside_stats():
    return _proxy("GET", "/v1/cache-aside/stats")


@app.route("/api/cache-aside/cache", methods=["DELETE"])
def cache_aside_clear():
    return _proxy("DELETE", "/v1/cache-aside/cache")


# ── Write-Through (2.4) proxy ────────────────────────────────────────────


@app.get("/api/write-through/profile/<int:user_id>")
def write_through_get(user_id: int):
    return _proxy("GET", f"/v1/write-through/profile/{user_id}")


@app.route("/api/write-through/profile/<int:user_id>", methods=["PUT"])
def write_through_put(user_id: int):
    return _proxy("PUT", f"/v1/write-through/profile/{user_id}")


@app.get("/api/write-through/stats")
def write_through_stats():
    return _proxy("GET", "/v1/write-through/stats")


@app.route("/api/write-through/cache", methods=["DELETE"])
def write_through_clear():
    return _proxy("DELETE", "/v1/write-through/cache")


# ── Write-Behind (2.5) proxy ────────────────────────────────────────────


@app.route("/api/write-behind/event", methods=["POST"])
def write_behind_post_event():
    return _proxy("POST", "/v1/write-behind/event")


@app.get("/api/write-behind/events")
def write_behind_get_events():
    return _proxy("GET", "/v1/write-behind/events")


@app.get("/api/write-behind/stats")
def write_behind_stats():
    return _proxy("GET", "/v1/write-behind/stats")


@app.route("/api/write-behind/flush", methods=["POST"])
def write_behind_flush():
    return _proxy("POST", "/v1/write-behind/flush")


@app.route("/api/write-behind/clear", methods=["DELETE"])
def write_behind_clear():
    return _proxy("DELETE", "/v1/write-behind/clear")


# ── Read-Through (2.6) proxy ──────────────────────────────────────────────────


@app.get("/api/read-through/article/<int:article_id>")
def read_through_get_article(article_id: int):
    return _proxy("GET", f"/v1/read-through/article/{article_id}")


@app.get("/api/read-through/stats")
def read_through_stats():
    return _proxy("GET", "/v1/read-through/stats")


@app.route("/api/read-through/cache", methods=["DELETE"])
def read_through_clear():
    return _proxy("DELETE", "/v1/read-through/cache")


# ── Response Caching Middleware (2.7) proxy ───────────────────────────────────
# Uses _proxy_xcache so the X-Cache header is forwarded to the browser.


@app.get("/api/middleware/catalog")
def middleware_catalog():
    return _proxy_xcache("GET", "/v1/middleware/catalog")


@app.get("/api/middleware/track/<int:track_id>")
def middleware_track(track_id: int):
    return _proxy_xcache("GET", f"/v1/middleware/track/{track_id}")


@app.get("/api/middleware/stats")
def middleware_stats():
    return _proxy("GET", "/v1/middleware/stats")


@app.route("/api/middleware/cache", methods=["DELETE"])
def middleware_clear():
    return _proxy("DELETE", "/v1/middleware/cache")


# ── HTTP Caching / ETag (2.8) proxy ─────────────────────────────────────────────────
# Uses _proxy_conditional so If-None-Match / If-Modified-Since and
# ETag / Last-Modified / Cache-Control headers are properly forwarded.


@app.get("/api/http-cache/article/<int:article_id>")
def http_cache_get_article(article_id: int):
    return _proxy_conditional("GET", f"/v1/http-cache/article/{article_id}")


@app.route("/api/http-cache/article/<int:article_id>", methods=["PUT"])
def http_cache_put_article(article_id: int):
    return _proxy("PUT", f"/v1/http-cache/article/{article_id}")


@app.get("/api/http-cache/articles")
def http_cache_list_articles():
    return _proxy_conditional("GET", "/v1/http-cache/articles")


@app.get("/api/http-cache/constants")
def http_cache_constants():
    return _proxy("GET", "/v1/http-cache/constants")


@app.get("/api/http-cache/stats")
def http_cache_stats():
    return _proxy("GET", "/v1/http-cache/stats")


@app.route("/api/http-cache/reset", methods=["POST"])
def http_cache_reset():
    return _proxy("POST", "/v1/http-cache/reset")


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return _proxy("GET", "/health")


# ── Async aiocache (2.9) proxy ───────────────────────────────────────────────


@app.get("/api/aiocache/recipe/<int:recipe_id>")
def aiocache_get_recipe(recipe_id: int):
    return _proxy("GET", f"/v1/aiocache/recipe/{recipe_id}")


@app.get("/api/aiocache/recipes")
def aiocache_batch_recipes():
    ids = request.args.get("ids", "1,2,3,4")
    return _proxy("GET", f"/v1/aiocache/recipes?ids={ids}")


@app.route("/api/aiocache/recipe/<int:recipe_id>", methods=["DELETE"])
def aiocache_evict_recipe(recipe_id: int):
    return _proxy("DELETE", f"/v1/aiocache/recipe/{recipe_id}")


@app.route("/api/aiocache/cache", methods=["DELETE"])
def aiocache_clear():
    return _proxy("DELETE", "/v1/aiocache/cache")


@app.get("/api/aiocache/stats")
def aiocache_stats():
    return _proxy("GET", "/v1/aiocache/stats")


@app.route("/api/aiocache/reset", methods=["POST"])
def aiocache_reset():
    return _proxy("POST", "/v1/aiocache/reset")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
