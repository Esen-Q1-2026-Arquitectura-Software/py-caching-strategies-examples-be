import json

import core.redis_client as _redis_mod
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

MC_TTL: int = 30  # seconds — short to make TTL expiry observable in the demo
MC_PREFIX: str = "mc:"  # Redis key prefix for cached responses
mc_hits: int = 0
mc_misses: int = 0


class ResponseCacheMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that caches full HTTP GET responses in Redis.

    Scope: only /v1/middleware/ routes (excluding /stats).
    Adds X-Cache: HIT or X-Cache: MISS header to every intercepted response.
    Route handlers under this prefix contain ZERO cache logic — this middleware
    is the entire caching layer.
    """

    async def dispatch(self, request: StarletteRequest, call_next):
        global mc_hits, mc_misses

        redis = _redis_mod._redis
        path = request.url.path

        # Only cache GET requests inside the middleware demo namespace.
        # Skip the /stats observability endpoint so it always reflects live data.
        if (
            request.method != "GET"
            or not path.startswith("/v1/middleware/")
            or path == "/v1/middleware/stats"
        ):
            return await call_next(request)

        # Build a deterministic cache key from path + query string.
        qs = str(request.query_params)
        cache_key = f"{MC_PREFIX}{path}?{qs}" if qs else f"{MC_PREFIX}{path}"

        # ── Cache HIT? ────────────────────────────────────────────────────────
        if redis:
            raw = await redis.get(cache_key)
            if raw:
                mc_hits += 1
                stored = json.loads(raw)
                try:
                    body_obj = json.loads(stored["body"])
                    body_obj["x_cache"] = "HIT"
                    body_obj["x_cache_key"] = cache_key
                    injected = json.dumps(body_obj).encode("utf-8")
                except (json.JSONDecodeError, KeyError):
                    injected = stored["body"].encode("utf-8")
                return StarletteResponse(
                    content=injected,
                    status_code=stored["status_code"],
                    headers={
                        **stored["headers"],
                        "X-Cache": "HIT",
                        "X-Cache-Key": cache_key,
                        "Content-Length": str(len(injected)),
                    },
                    media_type="application/json",
                )

        # ── Cache MISS — delegate to the route handler ────────────────────────
        response = await call_next(request)

        if response.status_code == 200 and redis:
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk

            safe_headers = {
                k: v
                for k, v in dict(response.headers).items()
                if k.lower() not in ("content-length", "transfer-encoding")
            }
            # Store the *clean* original body in Redis — no injected fields,
            # so future HIT responses also get a fresh injection above.
            await redis.setex(
                cache_key,
                MC_TTL,
                json.dumps(
                    {
                        "body": body_bytes.decode("utf-8"),
                        "status_code": response.status_code,
                        "headers": safe_headers,
                    }
                ),
            )
            mc_misses += 1
            try:
                body_obj = json.loads(body_bytes.decode("utf-8"))
                body_obj["x_cache"] = "MISS"
                body_obj["x_cache_key"] = cache_key
                injected = json.dumps(body_obj).encode("utf-8")
            except (json.JSONDecodeError, KeyError):
                injected = body_bytes
            return StarletteResponse(
                content=injected,
                status_code=response.status_code,
                headers={
                    **safe_headers,
                    "X-Cache": "MISS",
                    "X-Cache-Key": cache_key,
                    "Content-Length": str(len(injected)),
                },
                media_type="application/json",
            )

        mc_misses += 1
        return response
