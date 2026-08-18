"""Redis-backed fixed-window rate limiting for auth endpoints.

Fixed window (INCR + EXPIRE) rather than a sliding window — simplest thing
that works at this traffic scale, same "simplest heuristic that's still
correct" bias as e.g. the live-stats engine's turn-merging logic.
"""
from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import get_settings


def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    redis = get_redis()
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > limit:
        ttl = await redis.ttl(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
            headers={"Retry-After": str(max(ttl, 1))},
        )
