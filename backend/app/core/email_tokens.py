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
