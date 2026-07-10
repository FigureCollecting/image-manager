"""Simple in-memory token-bucket rate limiter middleware."""

import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from .config import get_settings

# ip -> (tokens_remaining, last_refill_timestamp)
_buckets: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        limit = settings.rate_limit_per_minute
        window = 60.0
        now = time.monotonic()
        ip = _client_ip(request)

        # Sliding window: keep timestamps within the window
        hits = _buckets[ip]
        # Purge expired entries
        cutoff = now - window
        _buckets[ip] = [t for t in hits if t > cutoff]
        hits = _buckets[ip]

        remaining = max(0, limit - len(hits))

        if len(hits) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={
                    "x-ratelimit-limit": str(limit),
                    "x-ratelimit-remaining": "0",
                    "retry-after": str(int(window - (now - hits[0]))),
                },
            )

        hits.append(now)
        response = await call_next(request)
        response.headers["x-ratelimit-limit"] = str(limit)
        response.headers["x-ratelimit-remaining"] = str(remaining - 1 if remaining > 0 else 0)
        return response
