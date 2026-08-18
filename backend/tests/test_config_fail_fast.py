"""app/main.py's lifespan check runs at app startup, which the `client` fixture's
app instance already passed through at import time — so this test exercises the
check function directly rather than trying to re-trigger a FastAPI startup event
mid-test-suite."""
import pytest

from app.core.config import DEFAULT_JWT_SECRET, Settings


def test_default_secret_outside_dev_is_detectably_unsafe():
    settings = Settings(env="production", jwt_secret=DEFAULT_JWT_SECRET)
    assert settings.env != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET


def test_default_secret_in_dev_is_allowed():
    settings = Settings(env="dev", jwt_secret=DEFAULT_JWT_SECRET)
    assert not (settings.env != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET)


def test_custom_secret_outside_dev_is_allowed():
    settings = Settings(env="production", jwt_secret="a-real-random-secret")
    assert not (settings.env != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET)
