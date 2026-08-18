import pytest
from fastapi import HTTPException

from app.core.rate_limit import enforce_rate_limit


async def test_requests_under_the_limit_pass():
    for _ in range(3):
        await enforce_rate_limit("test:under-limit", limit=3, window_seconds=60)


async def test_requests_over_the_limit_raise_429_with_retry_after():
    for _ in range(5):
        try:
            await enforce_rate_limit("test:over-limit", limit=5, window_seconds=60)
        except HTTPException:
            pytest.fail("should not raise until the 6th call")

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit("test:over-limit", limit=5, window_seconds=60)
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


async def test_different_keys_have_independent_limits():
    await enforce_rate_limit("test:key-a", limit=1, window_seconds=60)
    await enforce_rate_limit("test:key-b", limit=1, window_seconds=60)  # would raise if keys collided
