"""Shared pytest fixtures for API-level tests.

Runs against a real Postgres test database (same asyncpg/SQLAlchemy stack as
production — this project uses Postgres-specific types like UUID, not
sqlite). Each test is wrapped in an outer transaction that's rolled back
afterward via a SAVEPOINT (join_transaction_mode="create_savepoint"), so
tests never see each other's data even though the application code under
test calls session.commit() normally.
"""
import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.core.config import get_settings
from app.core.db import Base, get_db
from app.main import app as fastapi_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://gdtrainer:gdtrainer@localhost:5432/gdtrainer_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
# NullPool: pytest-asyncio gives the session-scoped _schema fixture and each
# test's function-scoped fixtures separate event loops by default. asyncpg
# connections are bound to the loop that created them, so a pooled
# connection checked out under a different loop than the one that opened it
# blows up. NullPool opens a fresh physical connection on every checkout.
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)


@pytest_asyncio.fixture(autouse=True)
async def _test_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    get_settings.cache_clear()
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()
    get_settings.cache_clear()

@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session_maker(_schema):
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session_maker = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        yield session_maker
        await trans.rollback()


@pytest_asyncio.fixture
async def db_session(db_session_maker):
    async with db_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
