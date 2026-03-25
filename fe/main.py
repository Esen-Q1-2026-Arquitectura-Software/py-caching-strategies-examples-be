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

API Proxies: (see routes below)
"""

import os

import httpx
from flask import Flask, jsonify, render_template, request

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


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return _proxy("GET", "/health")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
