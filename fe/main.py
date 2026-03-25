"""
Caching Showcase — Flask Frontend
====================================
Thin proxy layer that forwards requests to the FastAPI backend and
serves the interactive showcase UI.

Routes:
  GET  /                                    — main UI
  GET  /api/lru/country/<code>              — proxy → /v1/lru/country/<code>
  GET  /api/lru/stats                       — proxy → /v1/lru/stats
  DELETE /api/lru/cache                     — proxy → /v1/lru/cache
  GET  /api/ttl/user/<id>                   — proxy → /v1/ttl/user/<id>
  GET  /api/ttl/stats                       — proxy → /v1/ttl/stats
  DELETE /api/ttl/cache                     — proxy → /v1/ttl/cache
  GET  /api/redis/product/<id>              — proxy → /v1/redis/product/<id>
  GET  /api/redis/stats                     — proxy → /v1/redis/stats
  DELETE /api/redis/cache                   — proxy → /v1/redis/cache
  GET  /api/cache-aside/order/<id>          — proxy → /v1/cache-aside/order/<id>
  GET  /api/cache-aside/stats               — proxy → /v1/cache-aside/stats
  DELETE /api/cache-aside/cache             — proxy → /v1/cache-aside/cache
  GET  /api/write-through/profile/<id>      — proxy → /v1/write-through/profile/<id>
  PUT  /api/write-through/profile/<id>      — proxy → /v1/write-through/profile/<id>
  GET  /api/write-through/stats             — proxy → /v1/write-through/stats
  DELETE /api/write-through/cache           — proxy → /v1/write-through/cache
  GET  /api/health                          — proxy → /health
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
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.request(method, url)
        return jsonify(resp.json()), resp.status_code
    except httpx.RequestError as exc:
        return jsonify({"error": str(exc), "detail": "Backend unreachable"}), 502


# ── UI ────────────────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return render_template("index.html", backend_url=BACKEND_URL)


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


# ── Write-Through (2.4) proxy ─────────────────────────────────────────────────


@app.get("/api/write-through/profile/<int:user_id>")
def write_through_get_profile(user_id: int):
    return _proxy("GET", f"/v1/write-through/profile/{user_id}")


@app.route("/api/write-through/profile/<int:user_id>", methods=["PUT"])
def write_through_update_profile(user_id: int):
    """Forward PUT with JSON body to the backend."""
    url = f"{BACKEND_URL}/v1/write-through/profile/{user_id}"
    try:
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.put(url, json=request.get_json(force=True) or {})
        return jsonify(resp.json()), resp.status_code
    except httpx.RequestError as exc:
        return jsonify({"error": str(exc), "detail": "Backend unreachable"}), 502


@app.get("/api/write-through/stats")
def write_through_stats():
    return _proxy("GET", "/v1/write-through/stats")


@app.route("/api/write-through/cache", methods=["DELETE"])
def write_through_clear():
    return _proxy("DELETE", "/v1/write-through/cache")


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return _proxy("GET", "/health")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
