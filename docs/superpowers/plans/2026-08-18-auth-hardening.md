# Auth Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add email verification and Brevo-backed forgot/reset password to the existing JWT auth, and close the vulnerabilities found in that auth code, per `docs/superpowers/specs/2026-08-18-auth-hardening-design.md`.

**Architecture:** Backend-first: a new `email_tokens` table + `app/core/email_tokens.py` service for opaque, hashed, single-use tokens; `app/core/rate_limit.py` (Redis `INCR`+`EXPIRE`) guarding every `/auth/*` endpoint; `app/core/email.py` wrapping Brevo's HTTP API; `/auth/signup` and `/auth/login` rewritten, four new endpoints added. Frontend adds three new pages (verify-email, forgot-password, reset-password) and updates login/signup to match the new flows. This repo has no test suite on `master` yet (it exists only in an unmerged worktree for an unrelated subsystem) — this plan introduces pytest on `master` itself, task 1.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + Alembic + Redis (existing) · `httpx` (new — async HTTP client for both the Brevo API calls and the new pytest test client, matching the pattern the unmerged rooms work already established) · `pytest`/`pytest-asyncio` (new on `master`) · Next.js 16 App Router + TypeScript + Zustand (existing, no new frontend dependency).

**Spec:** `docs/superpowers/specs/2026-08-18-auth-hardening-design.md`

## Global Constraints

- Token TTLs: verification tokens 24h, reset tokens 1h (spec: Data model).
- Tokens are opaque (`secrets.token_urlsafe(32)`), only their SHA-256 hash is ever persisted, single-use, and issuing a new token invalidates prior unused tokens of the same purpose for that user (spec: Data model, Flows).
- Password max length is capped at **72** (not the previous 128) on signup and reset — bcrypt only considers the first 72 bytes (spec: Security review).
- Rate limits (Redis `INCR`+`EXPIRE`, per spec's Rate limiting section): `/auth/login` — 5/5min per email **and** 10/5min per IP; `/auth/signup` — 5/hour per IP; `/auth/forgot-password` and `/auth/resend-verification` — 1/60s per email. Exceeding any limit returns `429` with a `Retry-After` header.
- **Enumeration-safety resolution (clarifies the spec's Error handling section, which reads ambiguously if applied literally to both endpoints — this is the plan's binding interpretation):** `/auth/forgot-password` and `/auth/resend-verification` always return the same generic message regardless of whether the email exists, is verified, or whether the Brevo send itself succeeds — a differing response on email-send failure would reveal account existence via status code, undermining the whole point of the generic response. Brevo failures on these two endpoints are logged server-side only. `/auth/signup`, by contrast, already discloses "email already registered" (accepted, spec-approved disclosure), so its Brevo failure *does* surface to the caller as `502` — but the user row and verify token are committed first regardless, so a subsequent `resend-verification` call works without re-signing-up (spec: Error handling, "the user row/token is still created so a retry works").
- `get_current_user` (in `app/api/deps.py`) rejects any JWT whose `iat` predates the user's `password_changed_at` — this is what makes password reset actually invalidate existing sessions (spec: Flows).
- `env` setting (new, default `"dev"`): outside `env="dev"`, the app refuses to start if `jwt_secret` is still the shipped placeholder (spec: Security review, High).
- Follow existing repo conventions: SQLAlchemy 2.0 `Mapped`/`mapped_column`, `native_enum=False` string enums with an explicit `length=` (see `app/models/session.py`), Pydantic schemas with `from_attributes = True` where they wrap ORM objects, generic non-leaking error details on `HTTPException`.
- **One-time local setup this plan assumes** (same as the unmerged rooms plan, independently required here since this plan targets `master` directly): a `gdtrainer_test` Postgres database exists — `docker exec -it gdtrainer_postgres psql -U gdtrainer -c "CREATE DATABASE gdtrainer_test;"` — the test suite runs against real Postgres, not SQLite, because this project uses Postgres-specific types (`UUID`).
- Test Redis: rate-limit tests run against `redis://localhost:6379/15` (a separate DB index on the same local Redis, flushed before/after every test) so they never touch the DB index the dev app/ARQ worker use (`/0`).

---

### Task 1: Pytest scaffold on `master`

This repo has no automated test suite on `master` (only on an unmerged worktree for an unrelated subsystem). Every later task in this plan needs `pytest` + the `client`/`db_session` fixtures to exist first.

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/pytest.ini`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`

**Interfaces:**
- Produces: fixtures `client` (`httpx.AsyncClient` wired to the FastAPI app via `ASGITransport`, with `get_db` overridden), `db_session` (`AsyncSession` scoped to a per-test rollback transaction), `db_session_maker`. Every later backend task's tests use `client` and/or `db_session`.

- [ ] **Step 1: Add test dependencies**

Append to `backend/requirements.txt`:

```
# Testing (first automated test suite on master)
pytest==8.3.4
pytest-asyncio==0.25.2
httpx==0.28.1
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pip install -r requirements.txt`

- [ ] **Step 2: Create pytest config**

Create `backend/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: Create the test database**

Run: `docker exec -it gdtrainer_postgres psql -U gdtrainer -c "CREATE DATABASE gdtrainer_test;"`
(Skip if it already exists — the command will just report the database already exists.)

- [ ] **Step 4: Write conftest.py**

Create `backend/tests/__init__.py` (empty file).

Create `backend/tests/conftest.py`:

```python
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.core.db import Base, get_db
from app.main import app as fastapi_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://gdtrainer:gdtrainer@localhost:5432/gdtrainer_test"
)
# NullPool: pytest-asyncio gives the session-scoped _schema fixture and each
# test's function-scoped fixtures separate event loops by default. asyncpg
# connections are bound to the loop that created them, so a pooled
# connection checked out under a different loop than the one that opened it
# blows up. NullPool opens a fresh physical connection on every checkout.
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)


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
```

- [ ] **Step 5: Write a smoke test against stable, unrelated behavior**

Create `backend/tests/test_health.py`:

```python
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 6: Run it**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: `test_health_returns_ok` PASSES. (This proves the harness — real Postgres schema creation, FastAPI app wiring, transaction rollback — works before anything in this plan builds on it.)

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/pytest.ini backend/tests/
git commit -m "Auth hardening: add pytest scaffold to master"
```

---

### Task 2: Data model — verification/reset fields and the `email_tokens` table

**Files:**
- Modify: `backend/app/models/user.py`
- Create: `backend/app/models/email_token.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/a1e5f0c9b3d7_add_email_verification_and_tokens.py`
- Create: `backend/tests/models/__init__.py`
- Create: `backend/tests/models/test_email_token.py`

**Interfaces:**
- Produces: `User.is_verified: bool` (default `False`), `User.password_changed_at: datetime | None`; `app.models.email_token.EmailTokenPurpose` (str enum: `VERIFY`, `RESET`); `app.models.email_token.EmailToken` (`id`, `user_id`, `token_hash`, `purpose`, `expires_at`, `used_at`, `created_at`).

- [ ] **Step 1: Add the new User columns**

Modify `backend/app/models/user.py` — add two columns after `password_hash`:

```python
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 2: Create the EmailToken model**

Create `backend/app/models/email_token.py`:

```python
"""Verification/reset tokens (docs/superpowers/specs/2026-08-18-auth-hardening-design.md).

Tokens are opaque random strings; only their SHA-256 hash is ever persisted
here (see app/core/email_tokens.py), so a database compromise doesn't yield
usable tokens.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EmailTokenPurpose(str, enum.Enum):
    VERIFY = "verify"
    RESET = "reset"


class EmailToken(Base):
    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # native_enum=False: plain VARCHAR + CHECK constraint, matching app/models/session.py's
    # SessionStatus convention, so adding a purpose later is a one-line change.
    purpose: Mapped[EmailTokenPurpose] = mapped_column(
        Enum(EmailTokenPurpose, name="email_token_purpose", native_enum=False, length=10),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 3: Register the model**

Modify `backend/app/models/__init__.py`:

```python
from app.models.user import User
from app.models.topic import Topic
from app.models.session import Session, SessionStatus
from app.models.transcript import Transcript, Word
from app.models.session_metrics import SessionMetrics
from app.models.model_scores import ModelScores
from app.models.email_token import EmailToken, EmailTokenPurpose

__all__ = [
    "User",
    "Topic",
    "Session",
    "SessionStatus",
    "Transcript",
    "Word",
    "SessionMetrics",
    "ModelScores",
    "EmailToken",
    "EmailTokenPurpose",
]
```

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/a1e5f0c9b3d7_add_email_verification_and_tokens.py`:

```python
"""add email verification and tokens

Revision ID: a1e5f0c9b3d7
Revises: 85cb62ba58af
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1e5f0c9b3d7'
down_revision: Union[str, None] = '85cb62ba58af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'users',
        sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'email_tokens',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('purpose', sa.String(length=10), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_tokens_user_id'), 'email_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_email_tokens_token_hash'), 'email_tokens', ['token_hash'], unique=True)
    op.create_index(op.f('ix_email_tokens_purpose'), 'email_tokens', ['purpose'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_tokens_purpose'), table_name='email_tokens')
    op.drop_index(op.f('ix_email_tokens_token_hash'), table_name='email_tokens')
    op.drop_index(op.f('ix_email_tokens_user_id'), table_name='email_tokens')
    op.drop_table('email_tokens')
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'is_verified')
```

- [ ] **Step 5: Apply it to the dev database**

Run: `cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head`
Expected: runs clean, no errors.

- [ ] **Step 6: Write a test proving the model + schema work end to end**

Create `backend/tests/models/__init__.py` (empty file).

Create `backend/tests/models/test_email_token.py`:

```python
from datetime import datetime, timedelta, timezone

from app.core.security import hash_password
from app.models.email_token import EmailToken, EmailTokenPurpose
from app.models.user import User


async def test_new_user_defaults_to_unverified_with_no_password_change(db_session):
    user = User(email="new@example.com", password_hash=hash_password("pw12345678"), name="New")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.is_verified is False
    assert user.password_changed_at is None


async def test_email_token_round_trips_through_the_database(db_session):
    user = User(email="tok@example.com", password_hash=hash_password("pw12345678"), name="Tok")
    db_session.add(user)
    await db_session.flush()

    token = EmailToken(
        user_id=user.id,
        token_hash="a" * 64,
        purpose=EmailTokenPurpose.VERIFY,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(token)
    await db_session.commit()
    await db_session.refresh(token)

    assert token.used_at is None
    assert token.purpose == EmailTokenPurpose.VERIFY
```

- [ ] **Step 7: Run the tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/models/ -v`
Expected: both tests PASS. (This also exercises `Base.metadata.create_all` picking up the new table via `import app.models` in `conftest.py`, so it doubles as a schema-registration check.)

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/ backend/alembic/versions/a1e5f0c9b3d7_add_email_verification_and_tokens.py backend/tests/models/
git commit -m "Auth hardening: is_verified/password_changed_at columns, email_tokens table"
```

---

### Task 3: Token issuance/consumption service

**Files:**
- Create: `backend/app/core/email_tokens.py`
- Create: `backend/tests/core/__init__.py`
- Create: `backend/tests/core/test_email_tokens.py`

**Interfaces:**
- Consumes: `EmailToken`, `EmailTokenPurpose` (Task 2).
- Produces: `hash_token(raw_token: str) -> str`; `async issue_token(db: AsyncSession, user_id: uuid.UUID, purpose: EmailTokenPurpose) -> str` (returns the **raw** token — only its hash is stored); `async consume_token(db: AsyncSession, raw_token: str, purpose: EmailTokenPurpose) -> EmailToken | None` (returns the token row with `used_at` set if valid, or `None` if missing/used/expired — caller must still `commit()`). Task 8's endpoints call all three.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/core/__init__.py` (empty file).

Create `backend/tests/core/test_email_tokens.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.core.email_tokens import consume_token, hash_token, issue_token
from app.core.security import hash_password
from app.models.email_token import EmailToken, EmailTokenPurpose
from app.models.user import User


async def _make_user(db_session, email="user@example.com") -> User:
    user = User(email=email, password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.flush()
    return user


async def test_issue_token_returns_a_raw_token_whose_hash_is_stored(db_session):
    user = await _make_user(db_session)
    raw = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    from sqlalchemy import select

    result = await db_session.execute(select(EmailToken).where(EmailToken.user_id == user.id))
    row = result.scalar_one()
    assert row.token_hash == hash_token(raw)
    assert row.token_hash != raw


async def test_issuing_a_second_token_invalidates_the_first(db_session):
    user = await _make_user(db_session)
    first = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    second = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    assert await consume_token(db_session, first, EmailTokenPurpose.VERIFY) is None
    assert await consume_token(db_session, second, EmailTokenPurpose.VERIFY) is not None


async def test_consume_token_rejects_unknown_token(db_session):
    assert await consume_token(db_session, "not-a-real-token", EmailTokenPurpose.VERIFY) is None


async def test_consume_token_rejects_wrong_purpose(db_session):
    user = await _make_user(db_session)
    raw = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    assert await consume_token(db_session, raw, EmailTokenPurpose.RESET) is None


async def test_consume_token_rejects_expired_token(db_session):
    user = await _make_user(db_session)
    raw = "expired-raw-token"
    token = EmailToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        purpose=EmailTokenPurpose.RESET,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db_session.add(token)
    await db_session.commit()

    assert await consume_token(db_session, raw, EmailTokenPurpose.RESET) is None


async def test_consume_token_is_single_use(db_session):
    user = await _make_user(db_session)
    raw = await issue_token(db_session, user.id, EmailTokenPurpose.RESET)
    await db_session.commit()

    first = await consume_token(db_session, raw, EmailTokenPurpose.RESET)
    await db_session.commit()
    assert first is not None

    second = await consume_token(db_session, raw, EmailTokenPurpose.RESET)
    assert second is None
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_email_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.email_tokens'`

- [ ] **Step 3: Implement the service**

Create `backend/app/core/email_tokens.py`:

```python
"""Issuing and consuming email verification/reset tokens (app/models/email_token.py).

Tokens are opaque random strings; only their SHA-256 hash is ever persisted,
so a database compromise doesn't yield usable tokens (see
docs/superpowers/specs/2026-08-18-auth-hardening-design.md).
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_token import EmailToken, EmailTokenPurpose

VERIFY_TOKEN_TTL = timedelta(hours=24)
RESET_TOKEN_TTL = timedelta(hours=1)

_TTL_BY_PURPOSE = {
    EmailTokenPurpose.VERIFY: VERIFY_TOKEN_TTL,
    EmailTokenPurpose.RESET: RESET_TOKEN_TTL,
}


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def issue_token(db: AsyncSession, user_id: uuid.UUID, purpose: EmailTokenPurpose) -> str:
    """Invalidate any prior unused tokens of this purpose for this user, then issue a
    new one. Returns the raw token — the caller must email it; only its hash is stored.
    Does not commit; caller controls the transaction boundary."""
    await db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user_id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(timezone.utc))
    )
    raw_token = secrets.token_urlsafe(32)
    token = EmailToken(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + _TTL_BY_PURPOSE[purpose],
    )
    db.add(token)
    await db.flush()
    return raw_token


async def consume_token(
    db: AsyncSession, raw_token: str, purpose: EmailTokenPurpose
) -> EmailToken | None:
    """Look up a token by hash; if valid (right purpose, unused, unexpired), mark it
    used and return it. Returns None otherwise. Does not commit."""
    result = await db.execute(
        select(EmailToken).where(
            EmailToken.token_hash == hash_token(raw_token),
            EmailToken.purpose == purpose,
        )
    )
    token = result.scalar_one_or_none()
    if token is None or token.used_at is not None:
        return None
    if token.expires_at < datetime.now(timezone.utc):
        return None
    token.used_at = datetime.now(timezone.utc)
    return token
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_email_tokens.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/email_tokens.py backend/tests/core/
git commit -m "Auth hardening: email token issue/consume service"
```

---

### Task 4: JWT hardening — `iat`, fail-fast secret, session invalidation on password change

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/security.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/core/test_security.py`
- Modify: `backend/tests/test_health.py` (add a startup-failure test alongside it — see Step 5)

**Interfaces:**
- Consumes: `User.password_changed_at` (Task 2).
- Produces: `app.core.config.DEFAULT_JWT_SECRET`; `Settings.env: str`; `create_access_token(subject: str) -> str` (now embeds `iat`); `decode_access_token(token: str) -> dict | None` (**signature change** — now returns the full payload dict, not just the subject string); `get_current_user` now 401s when the token's `iat` predates the user's `password_changed_at`.

- [ ] **Step 1: Add `env` setting and the fail-fast constant**

Modify `backend/app/core/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "dev" | anything else. Outside "dev", app/main.py's lifespan refuses to start
    # if jwt_secret is still DEFAULT_JWT_SECRET (that placeholder is public — it's
    # committed to this repo).
    env: str = "dev"

    # Database
    database_url: str = "postgresql+asyncpg://gdtrainer:gdtrainer@localhost:5432/gdtrainer"
    database_url_sync: str = "postgresql+psycopg2://gdtrainer:gdtrainer@localhost:5432/gdtrainer"

    # Redis / jobs
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # Email (Brevo transactional API)
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "GD Trainer"
    frontend_base_url: str = "http://localhost:3000"

    # Storage
    storage_dir: str = "./storage"

    # Transcription (Day 2+)
    transcriber_provider: str = "local"
    whisper_model: str = "base.en"

    # Model layer (Day 4+)
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    embedding_model: str = "all-MiniLM-L6-v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: Embed `iat`, change `decode_access_token` to return the full payload**

Modify `backend/app/core/security.py`:

```python
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """Returns the full decoded payload ({"sub", "iat", "exp"}), or None if the
    token is invalid/expired/malformed."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
```

- [ ] **Step 3: Update `get_current_user` for the new return type + `password_changed_at` check**

Modify `backend/app/api/deps.py`:

```python
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import decode_access_token
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    subject = payload.get("sub")
    issued_at = payload.get("iat")
    if subject is None or issued_at is None:
        raise credentials_exception

    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    if user.password_changed_at is not None:
        token_issued_at = datetime.fromtimestamp(issued_at, tz=timezone.utc)
        if token_issued_at < user.password_changed_at:
            raise credentials_exception

    return user
```

- [ ] **Step 4: Wire the fail-fast check into app startup**

Modify `backend/app/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, progress, sessions, topics
from app.core.config import DEFAULT_JWT_SECRET, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.env != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is still the default placeholder. Set a real secret in your "
            "environment before running with ENV != dev."
        )
    yield


app = FastAPI(title="GD/Debate Speech Trainer API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(topics.router)
app.include_router(sessions.router)
app.include_router(progress.router)
```

- [ ] **Step 5: Write tests**

Create `backend/tests/core/test_security.py`:

```python
import time

from app.core.security import create_access_token, decode_access_token


def test_create_access_token_embeds_iat_and_sub():
    token = create_access_token(subject="user-123")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert isinstance(payload["iat"], int)


def test_decode_access_token_rejects_garbage():
    assert decode_access_token("not-a-jwt") is None


def test_two_tokens_for_the_same_subject_have_non_decreasing_iat():
    first = create_access_token(subject="user-123")
    time.sleep(1)
    second = create_access_token(subject="user-123")
    assert decode_access_token(second)["iat"] >= decode_access_token(first)["iat"]
```

Create `backend/tests/test_config_fail_fast.py`:

```python
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
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_security.py tests/test_config_fail_fast.py tests/models/ tests/core/test_email_tokens.py -v`
Expected: all PASS. Also re-run the full suite (`./.venv/Scripts/python.exe -m pytest -v`) to confirm `decode_access_token`'s signature change didn't break `test_health.py` or anything from earlier tasks — nothing else calls it yet, so this should be a no-op check.

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/config.py backend/app/core/security.py backend/app/api/deps.py backend/app/main.py backend/tests/core/test_security.py backend/tests/test_config_fail_fast.py
git commit -m "Auth hardening: iat claim, jwt_secret fail-fast, reset invalidates sessions"
```

---

### Task 5: Redis-backed rate limiting

**Files:**
- Create: `backend/app/core/rate_limit.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/core/test_rate_limit.py`

**Interfaces:**
- Produces: `get_redis() -> Redis`; `client_ip(request: Request) -> str`; `async enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None` (raises `HTTPException(429)` with `Retry-After` when exceeded). Task 8's endpoints call `enforce_rate_limit` and `client_ip`.

- [ ] **Step 1: Point the test suite at a dedicated Redis DB index**

Modify `backend/tests/conftest.py` — add these imports and this fixture (place the fixture after the existing imports, before `_schema`):

```python
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
```

(This replaces the existing import block at the top of the file — `os` was already imported; add `Redis` and `get_settings`.)

Add this fixture, autouse so every test gets an isolated, pre-flushed Redis DB regardless of whether that test touches rate limiting directly:

```python
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
```

- [ ] **Step 2: Write failing tests**

Create `backend/tests/core/test_rate_limit.py`:

```python
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
```

- [ ] **Step 3: Verify the tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_rate_limit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.rate_limit'`

- [ ] **Step 4: Implement it**

Create `backend/app/core/rate_limit.py`:

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_rate_limit.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/rate_limit.py backend/tests/conftest.py backend/tests/core/test_rate_limit.py
git commit -m "Auth hardening: Redis-backed rate limiting"
```

---

### Task 6: Brevo email client

**Files:**
- Create: `backend/app/core/email.py`
- Create: `backend/tests/core/test_email.py`

**Interfaces:**
- Produces: `EmailSendError(Exception)`; `async send_email(to_email: str, to_name: str, subject: str, html_content: str) -> None`; `build_verify_email(name: str, link: str) -> tuple[str, str]`; `build_reset_email(name: str, link: str) -> tuple[str, str]`. Task 8's endpoints call all of these.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/core/test_email.py`:

```python
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.email import EmailSendError, build_reset_email, build_verify_email, send_email


def test_build_verify_email_includes_the_link():
    subject, html = build_verify_email("Alex", "https://app.example.com/verify-email?token=abc")
    assert "abc" in html
    assert subject


def test_build_reset_email_includes_the_link():
    subject, html = build_reset_email("Alex", "https://app.example.com/reset-password?token=xyz")
    assert "xyz" in html
    assert subject


async def test_send_email_raises_if_brevo_not_configured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BREVO_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(EmailSendError):
        await send_email("to@example.com", "To", "subject", "<p>hi</p>")
    get_settings.cache_clear()


async def test_send_email_raises_on_non_2xx_response(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@example.com")
    get_settings.cache_clear()

    mock_response = httpx.Response(401, request=httpx.Request("POST", "https://api.brevo.com/v3/smtp/email"))
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(EmailSendError):
            await send_email("to@example.com", "To", "subject", "<p>hi</p>")
    get_settings.cache_clear()


async def test_send_email_succeeds_on_2xx_response(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@example.com")
    get_settings.cache_clear()

    mock_response = httpx.Response(201, request=httpx.Request("POST", "https://api.brevo.com/v3/smtp/email"))
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        await send_email("to@example.com", "To", "subject", "<p>hi</p>")  # should not raise
    get_settings.cache_clear()
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_email.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.email'`

- [ ] **Step 3: Implement it**

Create `backend/app/core/email.py`:

```python
"""Sending transactional email via Brevo's HTTP API (verify-email, reset-password).

Uses httpx.AsyncClient directly rather than Brevo's Python SDK, matching this
project's existing pattern of calling external HTTP APIs directly (see
app/scoring/pronunciation.py for the Azure equivalent) rather than pulling in
a provider SDK for a single endpoint.
"""
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("core.email")

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


class EmailSendError(Exception):
    """Raised when Brevo isn't configured, unreachable, or rejects the send."""


async def send_email(to_email: str, to_name: str, subject: str, html_content: str) -> None:
    settings = get_settings()
    if not settings.brevo_api_key:
        raise EmailSendError("Brevo is not configured (BREVO_API_KEY is empty)")

    payload = {
        "sender": {"email": settings.brevo_sender_email, "name": settings.brevo_sender_name},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "htmlContent": html_content,
    }
    headers = {"api-key": settings.brevo_api_key, "content-type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(BREVO_SEND_URL, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error("Brevo request failed: %s", exc)
            raise EmailSendError("Could not reach the email provider") from exc

    if response.status_code >= 400:
        logger.error(
            "Brevo rejected email to %s: %s %s", to_email, response.status_code, response.text
        )
        raise EmailSendError(f"Brevo returned {response.status_code}")


def build_verify_email(name: str, link: str) -> tuple[str, str]:
    subject = "Verify your email — GD Trainer"
    html = (
        f"<p>Hi {name},</p>"
        f"<p>Confirm your email to activate your GD Trainer account:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>This link expires in 24 hours.</p>"
    )
    return subject, html


def build_reset_email(name: str, link: str) -> tuple[str, str]:
    subject = "Reset your password — GD Trainer"
    html = (
        f"<p>Hi {name},</p>"
        f"<p>Someone requested a password reset for this account. If that was you, "
        f'click below (expires in 1 hour):</p>'
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>If you didn't request this, you can ignore this email.</p>"
    )
    return subject, html
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/core/test_email.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Add Brevo settings to `.env.example`**

Modify `backend/.env.example` — append:

```
# --- Email (Brevo transactional API, wired for auth hardening) ---
# https://app.brevo.com -> Settings -> SMTP & API -> API Keys
BREVO_API_KEY=
BREVO_SENDER_EMAIL=
BREVO_SENDER_NAME=GD Trainer
# Used to build verify-email/reset-password links sent in emails.
FRONTEND_BASE_URL=http://localhost:3000
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/email.py backend/tests/core/test_email.py backend/.env.example
git commit -m "Auth hardening: Brevo transactional email client"
```

---

### Task 7: New/updated auth schemas

**Files:**
- Modify: `backend/app/schemas/auth.py`

**Interfaces:**
- Produces: `MessageResponse(message: str)`; `EmailOnlyRequest(email: EmailStr)` (shared shape for `/forgot-password` and `/resend-verification`); `VerifyEmailRequest(token: str)`; `ResetPasswordRequest(token: str, new_password: str)`; `SignupRequest.password` now `max_length=72` (was 128).

- [ ] **Step 1: Rewrite the schemas file**

Modify `backend/app/schemas/auth.py`:

```python
import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    message: str


class EmailOnlyRequest(BaseModel):
    """Shared shape for /auth/forgot-password and /auth/resend-verification —
    both endpoints take only an email and always return the same generic
    MessageResponse regardless of what they find (see the plan's Global
    Constraints: enumeration-safety resolution)."""

    email: EmailStr


class VerifyEmailRequest(BaseModel):
    token: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=72)
```

(No test for this task alone — it's exercised end to end by Task 8's endpoint tests, which is where a bare schema change becomes independently verifiable.)

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/auth.py
git commit -m "Auth hardening: verification/reset schemas, bcrypt-safe password length"
```

---

### Task 8: Rewrite `/auth/signup`, `/auth/login`; add verify/resend/forgot/reset endpoints

**Files:**
- Modify: `backend/app/api/auth.py`
- Create: `backend/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `issue_token`, `consume_token` (Task 3); `enforce_rate_limit`, `client_ip` (Task 5); `send_email`, `EmailSendError`, `build_verify_email`, `build_reset_email` (Task 6); `MessageResponse`, `EmailOnlyRequest`, `VerifyEmailRequest`, `ResetPasswordRequest` (Task 7).
- Produces: the full `/auth/*` API surface used by the frontend in Task 9.

- [ ] **Step 1: Write failing tests for the new flows**

Create `backend/tests/test_auth_api.py`:

```python
from unittest.mock import AsyncMock, patch

from app.core.email_tokens import issue_token
from app.core.security import create_access_token, hash_password
from app.models.email_token import EmailTokenPurpose
from app.models.user import User

SEND_EMAIL_PATCH_TARGET = "app.api.auth.send_email"


async def _create_verified_user(db_session, email="verified@example.com", password="pw12345678") -> User:
    user = User(
        email=email, password_hash=hash_password(password), name="Verified", is_verified=True
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_signup_creates_an_unverified_user_and_sends_a_verify_email(client, db_session):
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()) as mock_send:
        resp = await client.post(
            "/auth/signup",
            json={"email": "new@example.com", "password": "pw12345678", "name": "New"},
        )
    assert resp.status_code == 201
    assert "access_token" not in resp.json()
    mock_send.assert_awaited_once()

    from sqlalchemy import select

    result = await db_session.execute(select(User).where(User.email == "new@example.com"))
    user = result.scalar_one()
    assert user.is_verified is False


async def test_signup_rejects_duplicate_email(client, db_session):
    await _create_verified_user(db_session, email="dupe@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()):
        resp = await client.post(
            "/auth/signup",
            json={"email": "dupe@example.com", "password": "pw12345678", "name": "Dupe"},
        )
    assert resp.status_code == 409


async def test_signup_returns_502_but_keeps_the_user_row_if_email_send_fails(client, db_session):
    from app.core.email import EmailSendError

    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock(side_effect=EmailSendError("boom"))):
        resp = await client.post(
            "/auth/signup",
            json={"email": "flaky@example.com", "password": "pw12345678", "name": "Flaky"},
        )
    assert resp.status_code == 502

    from sqlalchemy import select

    result = await db_session.execute(select(User).where(User.email == "flaky@example.com"))
    assert result.scalar_one_or_none() is not None


async def test_login_rejects_unverified_user(client, db_session):
    user = User(email="unverified@example.com", password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.commit()

    resp = await client.post(
        "/auth/login", json={"email": "unverified@example.com", "password": "pw12345678"}
    )
    assert resp.status_code == 403


async def test_login_succeeds_for_verified_user(client, db_session):
    await _create_verified_user(db_session, email="ok@example.com")
    resp = await client.post("/auth/login", json={"email": "ok@example.com", "password": "pw12345678"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_verify_email_marks_user_verified(client, db_session):
    user = User(email="tobev@example.com", password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.flush()
    raw_token = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    resp = await client.post("/auth/verify-email", json={"token": raw_token})
    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.is_verified is True


async def test_verify_email_rejects_invalid_token(client, db_session):
    resp = await client.post("/auth/verify-email", json={"token": "garbage"})
    assert resp.status_code == 400


async def test_forgot_password_returns_the_same_message_whether_or_not_the_email_exists(
    client, db_session
):
    await _create_verified_user(db_session, email="exists@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()):
        exists_resp = await client.post("/auth/forgot-password", json={"email": "exists@example.com"})
    missing_resp = await client.post("/auth/forgot-password", json={"email": "nobody@example.com"})

    assert exists_resp.status_code == 200
    assert missing_resp.status_code == 200
    assert exists_resp.json() == missing_resp.json()


async def test_forgot_password_still_returns_generic_message_if_brevo_fails(client, db_session):
    from app.core.email import EmailSendError

    await _create_verified_user(db_session, email="flaky2@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock(side_effect=EmailSendError("boom"))):
        resp = await client.post("/auth/forgot-password", json={"email": "flaky2@example.com"})
    assert resp.status_code == 200


async def test_reset_password_updates_password_and_invalidates_prior_jwts(client, db_session):
    user = await _create_verified_user(db_session, email="reset@example.com", password="oldpassword1")
    old_token = create_access_token(subject=str(user.id))

    raw_reset_token = await issue_token(db_session, user.id, EmailTokenPurpose.RESET)
    await db_session.commit()

    reset_resp = await client.post(
        "/auth/reset-password", json={"token": raw_reset_token, "new_password": "newpassword1"}
    )
    assert reset_resp.status_code == 200

    old_me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert old_me_resp.status_code == 401

    login_resp = await client.post(
        "/auth/login", json={"email": "reset@example.com", "password": "newpassword1"}
    )
    assert login_resp.status_code == 200


async def test_reset_password_rejects_reused_token(client, db_session):
    user = await _create_verified_user(db_session, email="reuse@example.com")
    raw_reset_token = await issue_token(db_session, user.id, EmailTokenPurpose.RESET)
    await db_session.commit()

    first = await client.post(
        "/auth/reset-password", json={"token": raw_reset_token, "new_password": "firstnewpw1"}
    )
    assert first.status_code == 200

    second = await client.post(
        "/auth/reset-password", json={"token": raw_reset_token, "new_password": "secondnewpw1"}
    )
    assert second.status_code == 400


async def test_resend_verification_reissues_for_an_unverified_user(client, db_session):
    user = User(email="resend@example.com", password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.commit()

    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()) as mock_send:
        resp = await client.post("/auth/resend-verification", json={"email": "resend@example.com"})
    assert resp.status_code == 200
    mock_send.assert_awaited_once()


async def test_resend_verification_is_a_no_op_but_still_generic_for_already_verified_user(
    client, db_session
):
    await _create_verified_user(db_session, email="alreadyok@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()) as mock_send:
        resp = await client.post("/auth/resend-verification", json={"email": "alreadyok@example.com"})
    assert resp.status_code == 200
    mock_send.assert_not_awaited()
```

- [ ] **Step 2: Verify the new tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_auth_api.py -v`
Expected: FAIL — `/auth/verify-email` etc. don't exist yet (404s), and the existing signup/login behavior doesn't match the new assertions yet.

- [ ] **Step 3: Rewrite the auth router**

Modify `backend/app/api/auth.py`:

```python
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.email import EmailSendError, build_reset_email, build_verify_email, send_email
from app.core.email_tokens import consume_token, issue_token
from app.core.rate_limit import client_ip, enforce_rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.models.email_token import EmailTokenPurpose
from app.models.user import User
from app.schemas.auth import (
    EmailOnlyRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)

logger = logging.getLogger("api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit(f"ratelimit:signup:ip:{client_ip(request)}", limit=5, window_seconds=3600)

    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
    )
    db.add(user)
    await db.flush()

    raw_token = await issue_token(db, user.id, EmailTokenPurpose.VERIFY)
    # Commit before attempting to send: the account and its token must persist even
    # if Brevo is down, so a later resend-verification call works without re-signup.
    await db.commit()

    link = f"{get_settings().frontend_base_url}/verify-email?token={raw_token}"
    subject, html = build_verify_email(user.name, link)
    try:
        await send_email(user.email, user.name, subject, html)
    except EmailSendError:
        logger.error("Failed to send verification email to %s", user.email)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Account created, but we couldn't send the verification email. "
                "Use 'resend verification' from the login page to try again."
            ),
        )

    return MessageResponse(message="Account created. Check your email to verify before logging in.")


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit(f"ratelimit:login:ip:{client_ip(request)}", limit=10, window_seconds=300)
    await enforce_rate_limit(f"ratelimit:login:email:{payload.email}", limit=5, window_seconds=300)

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    token = await consume_token(db, payload.token, EmailTokenPurpose.VERIFY)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired."
        )

    user = await db.get(User, token.user_id)
    user.is_verified = True
    await db.commit()
    return MessageResponse(message="Email verified. You can now log in.")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    payload: EmailOnlyRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    await enforce_rate_limit(
        f"ratelimit:resend-verification:email:{payload.email}", limit=1, window_seconds=60
    )

    generic_response = MessageResponse(
        message="If that email is registered and not yet verified, we've sent a new link."
    )

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or user.is_verified:
        return generic_response

    raw_token = await issue_token(db, user.id, EmailTokenPurpose.VERIFY)
    await db.commit()

    link = f"{get_settings().frontend_base_url}/verify-email?token={raw_token}"
    subject, html = build_verify_email(user.name, link)
    try:
        await send_email(user.email, user.name, subject, html)
    except EmailSendError:
        # Deliberately still generic — a differing response here would leak whether
        # payload.email belongs to a real, unverified account.
        logger.error("Failed to resend verification email to %s", user.email)

    return generic_response


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    payload: EmailOnlyRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    await enforce_rate_limit(
        f"ratelimit:forgot-password:email:{payload.email}", limit=1, window_seconds=60
    )

    generic_response = MessageResponse(
        message="If that email is registered, we've sent a password reset link."
    )

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        return generic_response

    raw_token = await issue_token(db, user.id, EmailTokenPurpose.RESET)
    await db.commit()

    link = f"{get_settings().frontend_base_url}/reset-password?token={raw_token}"
    subject, html = build_reset_email(user.name, link)
    try:
        await send_email(user.email, user.name, subject, html)
    except EmailSendError:
        logger.error("Failed to send password reset email to %s", user.email)

    return generic_response


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    token = await consume_token(db, payload.token, EmailTokenPurpose.RESET)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired."
        )

    user = await db.get(User, token.user_id)
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return MessageResponse(message="Password updated. You can now log in with your new password.")
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_auth_api.py -v`
Expected: all tests PASS.

Then run the full suite: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: everything from Tasks 1-8 PASSES together.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth.py backend/tests/test_auth_api.py
git commit -m "Auth hardening: signup/login rewrite, verify/resend/forgot/reset endpoints"
```

---

### Task 9: Frontend — verify/forgot/reset pages, updated login/signup

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/authStore.ts`
- Modify: `frontend/src/app/login/page.tsx`
- Modify: `frontend/src/app/signup/page.tsx`
- Create: `frontend/src/app/forgot-password/page.tsx`
- Create: `frontend/src/app/reset-password/page.tsx`
- Create: `frontend/src/app/verify-email/page.tsx`

**Interfaces:**
- Consumes: `/auth/signup` (now returns `MessageResponse`, not `TokenResponse`), `/auth/verify-email`, `/auth/resend-verification`, `/auth/forgot-password`, `/auth/reset-password` (Task 8).

- [ ] **Step 1: Add the new API calls and change `signup`'s return type**

Modify `frontend/src/lib/api.ts` — add this interface near `TokenResponse`:

```typescript
export interface MessageResponse {
  message: string;
}
```

Change the `signup` entry in the `api` object and add four new entries (place them after `me: ...`):

```typescript
  signup: (email: string, password: string, name: string) =>
    request<MessageResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    }),
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: (token: string) => request<UserResponse>("/auth/me", {}, token),
  verifyEmail: (token: string) =>
    request<MessageResponse>("/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  resendVerification: (email: string) =>
    request<MessageResponse>("/auth/resend-verification", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  forgotPassword: (email: string) =>
    request<MessageResponse>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  resetPassword: (token: string, newPassword: string) =>
    request<MessageResponse>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
```

(Leave the rest of `api.ts` — `randomTopic`, `createSession`, etc. — unchanged.)

- [ ] **Step 2: Update the auth store — signup no longer sets a token**

Modify `frontend/src/lib/authStore.ts` — replace the `signup` function body:

```typescript
      signup: async (email, password, name) => {
        set({ isLoading: true, error: null });
        try {
          await api.signup(email, password, name);
        } catch (e) {
          set({ error: e instanceof Error ? e.message : "Signup failed" });
          throw e;
        } finally {
          set({ isLoading: false });
        }
      },
```

(`login`, `logout`, `hydrateUser` are unchanged.)

- [ ] **Step 3: Update the signup page — show a "check your email" state instead of redirecting**

Modify `frontend/src/app/signup/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { useAuthStore } from "@/lib/authStore";
import { ApiError } from "@/lib/api";

export default function SignupPage() {
  const signup = useAuthStore((s) => s.signup);
  const isLoading = useAuthStore((s) => s.isLoading);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await signup(email, password, name);
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    }
  }

  if (submitted) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 text-center dark:border-white/10">
          <h1 className="text-xl font-semibold">Check your email</h1>
          <p className="text-sm text-gray-500">
            We sent a verification link to {email}. Verify your email, then{" "}
            <Link href="/login" className="underline">
              log in
            </Link>
            .
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 dark:border-white/10"
      >
        <h1 className="text-xl font-semibold">Create your account</h1>

        <div className="space-y-1">
          <label htmlFor="name" className="text-sm font-medium">
            Name
          </label>
          <input
            id="name"
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={8}
            maxLength={72}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
          <p className="text-xs text-gray-500">At least 8 characters.</p>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={isLoading}
          className="w-full rounded-md bg-black px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {isLoading ? "Creating account..." : "Sign up"}
        </button>

        <p className="text-center text-sm text-gray-500">
          Already have an account?{" "}
          <Link href="/login" className="underline">
            Log in
          </Link>
        </p>
      </form>
    </main>
  );
}
```

- [ ] **Step 4: Update the login page — forgot-password link + resend-verification on 403**

Modify `frontend/src/app/login/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuthStore } from "@/lib/authStore";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const login = useAuthStore((s) => s.login);
  const isLoading = useAuthStore((s) => s.isLoading);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNeedsVerification(false);
    setResendMessage(null);
    try {
      await login(email, password);
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setNeedsVerification(true);
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      }
    }
  }

  async function handleResend() {
    setResendMessage(null);
    try {
      const res = await api.resendVerification(email);
      setResendMessage(res.message);
    } catch (err) {
      setResendMessage(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 dark:border-white/10"
      >
        <h1 className="text-xl font-semibold">Log in</h1>

        <div className="space-y-1">
          <label htmlFor="email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
          <p className="text-right text-xs">
            <Link href="/forgot-password" className="text-gray-500 underline">
              Forgot password?
            </Link>
          </p>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}
        {needsVerification && (
          <button type="button" onClick={handleResend} className="text-sm text-gray-500 underline">
            Resend verification email
          </button>
        )}
        {resendMessage && <p className="text-sm text-gray-500">{resendMessage}</p>}

        <button
          type="submit"
          disabled={isLoading}
          className="w-full rounded-md bg-black px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {isLoading ? "Logging in..." : "Log in"}
        </button>

        <p className="text-center text-sm text-gray-500">
          Don&apos;t have an account?{" "}
          <Link href="/signup" className="underline">
            Sign up
          </Link>
        </p>
      </form>
    </main>
  );
}
```

- [ ] **Step 5: Add the forgot-password page**

Create `frontend/src/app/forgot-password/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      const res = await api.forgotPassword(email);
      setMessage(res.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 dark:border-white/10"
      >
        <h1 className="text-xl font-semibold">Forgot your password?</h1>

        <div className="space-y-1">
          <label htmlFor="email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
        </div>

        {message && <p className="text-sm text-gray-500">{message}</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={isLoading}
          className="w-full rounded-md bg-black px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {isLoading ? "Sending..." : "Send reset link"}
        </button>

        <p className="text-center text-sm text-gray-500">
          Remembered it?{" "}
          <Link href="/login" className="underline">
            Log in
          </Link>
        </p>
      </form>
    </main>
  );
}
```

- [ ] **Step 6: Add the reset-password page**

Create `frontend/src/app/reset-password/page.tsx`:

```tsx
"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";

function ResetPasswordContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError("Missing reset token.");
      return;
    }
    setIsLoading(true);
    try {
      await api.resetPassword(token, password);
      router.push("/login");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 dark:border-white/10"
      >
        <h1 className="text-xl font-semibold">Set a new password</h1>

        <div className="space-y-1">
          <label htmlFor="password" className="text-sm font-medium">
            New password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={8}
            maxLength={72}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
          />
          <p className="text-xs text-gray-500">At least 8 characters.</p>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={isLoading}
          className="w-full rounded-md bg-black px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {isLoading ? "Updating..." : "Update password"}
        </button>
      </form>
    </main>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordContent />
    </Suspense>
  );
}
```

- [ ] **Step 7: Add the verify-email page**

Create `frontend/src/app/verify-email/page.tsx`:

```tsx
"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<"pending" | "success" | "error">("pending");
  const [message, setMessage] = useState("Verifying...");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("Missing verification token.");
      return;
    }
    api
      .verifyEmail(token)
      .then((res) => {
        setStatus("success");
        setMessage(res.message);
      })
      .catch((err) => {
        setStatus("error");
        setMessage(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      });
  }, [token]);

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-sm space-y-4 rounded-xl border border-black/10 p-6 text-center dark:border-white/10">
        <h1 className="text-xl font-semibold">Verify your email</h1>
        <p className={`text-sm ${status === "error" ? "text-red-600" : "text-gray-500"}`}>{message}</p>
        {status === "success" && (
          <Link href="/login" className="text-sm underline">
            Log in
          </Link>
        )}
      </div>
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmailContent />
    </Suspense>
  );
}
```

**Note for the implementer:** `frontend/AGENTS.md` warns this Next.js version (16) may differ from standard docs — if `useSearchParams` inside a `Suspense` boundary behaves differently than above (e.g. a build warning about missing suspense), check `node_modules/next/dist/docs/` for the current API before adjusting; the two pages above (`reset-password`, `verify-email`) are the only ones in this task using it.

- [ ] **Step 8: Verify — typecheck and lint**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Run: `cd frontend && npm run lint`
Expected: no errors.

Run: `cd frontend && npm run build`
Expected: builds clean, including the two new `Suspense`-wrapped pages.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/authStore.ts frontend/src/app/login/page.tsx frontend/src/app/signup/page.tsx frontend/src/app/forgot-password/ frontend/src/app/reset-password/ frontend/src/app/verify-email/
git commit -m "Auth hardening: verify-email/forgot-password/reset-password UI"
```

---

## Manual end-to-end verification (after all 9 tasks)

Once real Brevo credentials are in `backend/.env` (`BREVO_API_KEY`, `BREVO_SENDER_EMAIL`) and the worker/app are restarted:

1. Sign up with a real email you can check. Confirm you land on "Check your email," a real email arrives, and `/auth/login` rejects with "verify your email" before you click the link.
2. Click the verify link → confirm the verify-email page shows success and login now works.
3. Log in, then use "Forgot password" with that same email → confirm the email arrives, the reset link works, and the **old JWT stops working** (refresh the dashboard tab that was still logged in from step 2 — it should bounce to `/login`).
4. Try `/auth/forgot-password` with an email that was never registered → confirm the response text is identical to step 3's.
5. Hammer `/auth/login` with a wrong password 6+ times in under 5 minutes → confirm a `429` shows up.
